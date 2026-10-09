import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cache_miss_notifier import PRIME_ACTIVE_SECONDS, Watcher  # noqa: E402


def record(event_type, payload):
    return (json.dumps({"type": event_type, "payload": payload}, ensure_ascii=False) + "\n").encode("utf-8")


def usage(cached):
    return record("event_msg", {
        "type": "token_count",
        "info": {"last_token_usage": {
            "input_tokens": 1000,
            "cached_input_tokens": cached,
        }},
    })


class ReadUpdatesTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "session.jsonl"
        self.notifications = []
        self.watcher = Watcher(
            self.path.parent,
            lambda title, body: self.notifications.append((title, body)),
        )

    def append(self, data):
        with self.path.open("ab") as stream:
            stream.write(data)

    def assert_one_miss(self):
        self.assertEqual(len(self.notifications), 1)
        self.assertEqual(self.notifications[0][0], "Oops! Cache miss!")
        self.watcher.read_updates(self.path)
        self.assertEqual(len(self.notifications), 1)
        self.assertEqual(self.watcher.states[self.path].offset, self.path.stat().st_size)

    def test_partial_append_is_delivered_once_after_completion(self):
        baseline, miss = usage(500), usage(0)
        self.path.write_bytes(baseline + miss[:30])
        self.watcher.read_updates(self.path)
        self.assertEqual(self.notifications, [])
        self.assertEqual(self.watcher.states[self.path].offset, len(baseline))
        self.watcher.read_updates(self.path)
        self.assertEqual(self.watcher.states[self.path].offset, len(baseline))
        self.append(miss[30:])
        self.watcher.read_updates(self.path)
        self.assert_one_miss()

    def test_valid_json_waits_for_newline(self):
        baseline, miss = usage(500), usage(0)
        self.path.write_bytes(baseline + miss[:-1])
        self.watcher.read_updates(self.path)
        self.assertEqual(self.notifications, [])
        self.assertEqual(self.watcher.states[self.path].offset, len(baseline))
        self.append(b"\n")
        self.watcher.read_updates(self.path)
        self.assert_one_miss()

    def test_startup_preserves_incomplete_tail_without_replaying_history(self):
        history, miss = usage(500) + usage(0), usage(0)
        self.path.write_bytes(history + miss[:30])
        self.watcher.prime_existing()
        self.assertEqual(self.notifications, [])
        self.assertEqual(self.watcher.states[self.path].offset, len(history))
        self.append(miss[30:])
        self.watcher.read_updates(self.path)
        self.assert_one_miss()

    def test_startup_does_not_consume_json_without_newline(self):
        baseline = usage(500)
        self.path.write_bytes(baseline[:-1])
        self.watcher.prime_existing()
        state = self.watcher.states[self.path]
        self.assertFalse(state.baseline.cache_seen)
        self.assertEqual(state.offset, 0)
        self.append(b"\n" + usage(0))
        self.watcher.read_updates(self.path)
        self.assert_one_miss()

    def test_utf8_split_preserves_metadata_and_complete_prefix(self):
        for prime in (False, True):
            with self.subTest(prime=prime):
                self.notifications.clear()
                self.watcher.states.clear()
                originator = "测试客户端"
                metadata = record("session_meta", {"originator": originator})
                split = metadata.index(originator.encode("utf-8")) + 1
                baseline = usage(500)
                self.path.write_bytes(baseline + metadata[:split])
                if prime:
                    self.watcher.prime_existing()
                else:
                    self.watcher.read_updates(self.path)
                state = self.watcher.states[self.path]
                self.assertTrue(state.baseline.cache_seen)
                self.assertEqual(state.offset, len(baseline))
                self.assertEqual(state.source, "Codex")
                self.append(metadata[split:] + usage(0))
                self.watcher.read_updates(self.path)
                self.assert_one_miss()
                self.assertIn(originator, self.notifications[0][1])

    def test_inactive_startup_preserves_tail_without_loading_history(self):
        for history in (b"", usage(500) + b"{}\n" * 2000):
            with self.subTest(history_bytes=len(history)):
                self.notifications.clear()
                self.watcher.states.clear()
                baseline = usage(500)[:-1] + b" " * 9000 + b"\n"
                self.path.write_bytes(history + baseline[:-1])
                old = time.time() - PRIME_ACTIVE_SECONDS - 60
                os.utime(self.path, (old, old))
                self.watcher.prime_existing()
                state = self.watcher.states[self.path]
                self.assertFalse(state.baseline.cache_seen)
                self.assertEqual(state.offset, len(history))
                self.append(b"\n" + usage(0))
                self.watcher.read_updates(self.path)
                self.assert_one_miss()

    def test_malformed_complete_record_does_not_block_later_records(self):
        self.path.write_bytes(usage(500) + b"not JSON\n" + usage(0))
        self.watcher.read_updates(self.path)
        self.assert_one_miss()


if __name__ == "__main__":
    unittest.main()
