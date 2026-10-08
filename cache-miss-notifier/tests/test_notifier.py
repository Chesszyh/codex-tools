import json
from pathlib import Path
import sys
import tempfile
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cache_miss_notifier import CodexSessionState  # noqa: E402


def event(event_type, payload):
    return json.dumps({"type": event_type, "payload": payload})


class CodexSessionStateTests(unittest.TestCase):
    def setUp(self):
        self.state = CodexSessionState(Path("session.jsonl"))
        self.state.consume(event("session_meta", {"originator": "Codex Desktop"}))
        self.state.consume(event("turn_context", {"model": "gpt-test"}))
        self.state.consume(event("event_msg", {"type": "task_started", "model_context_window": 400_000}))

    def token_count(self, cached, input_tokens=500_000, cache_write=0):
        return self.state.consume(
            event(
                "event_msg",
                {
                    "type": "token_count",
                    "info": {
                        "last_token_usage": {
                            "input_tokens": input_tokens,
                            "cached_input_tokens": cached,
                            "cache_write_input_tokens": cache_write,
                        }
                    },
                },
            )
        )

    def establish_cache_baseline(self):
        self.assertIsNone(self.token_count(100_000))

    def test_first_uncached_request_is_silent(self):
        self.assertIsNone(self.token_count(0))

    def test_small_cache_hit_is_silent(self):
        self.assertIsNone(self.token_count(100_000))

    def test_cache_hit_at_threshold_is_silent(self):
        self.assertIsNone(self.token_count(300_000))

    def test_cache_hit_over_threshold_notifies(self):
        title, body = self.token_count(300_001)
        self.assertEqual(title, "Cache hit!")
        self.assertIn("cached 300,001", body)

    def test_cache_hit_threshold_updates_with_context_window(self):
        self.state.consume(event("event_msg", {"type": "task_started", "model_context_window": 200_000}))
        self.assertIsNone(self.token_count(150_000))
        self.assertEqual(self.token_count(150_001)[0], "Cache hit!")

    def test_cache_hit_without_context_window_is_silent(self):
        self.state.consume(event("event_msg", {"type": "task_started"}))
        self.assertIsNone(self.token_count(400_000))

    def test_prime_reads_context_window_before_long_turn(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "session.jsonl"
            path.write_text(
                event("event_msg", {"type": "task_started", "model_context_window": 400_000})
                + "\n"
                + ("{}\n" * 100_000)
            )
            state = CodexSessionState(path)
            state.prime()
            self.assertEqual(state.context_window, 400_000)

    def test_zero_cache_after_observed_hit_notifies(self):
        self.establish_cache_baseline()
        title, body = self.token_count(0)
        self.assertEqual(title, "Oops! Cache miss!")
        self.assertIn("Codex Desktop · gpt-test", body)
        self.assertIn("cached 0", body)
        self.assertNotIn("reason", body.lower())

    def test_cache_write_establishes_baseline(self):
        self.assertIsNone(self.token_count(0, cache_write=100_000))
        self.assertIsNotNone(self.token_count(0))

    def test_compaction_resets_baseline(self):
        self.establish_cache_baseline()
        self.state.consume(json.dumps({"type": "compacted", "payload": {}}))
        self.assertIsNone(self.token_count(0))

    def test_upstream_miss_reason_is_included(self):
        self.establish_cache_baseline()
        self.state.consume(
            event(
                "event_msg",
                {
                    "type": "response_metadata",
                    "prompt_cache_diagnostics": {
                        "type": "cache_miss",
                        "reason": "model_changed",
                        "cache_missed_tokens": 2048,
                        "comparison_reusable_tokens": 1024,
                    },
                },
            )
        )
        _, body = self.token_count(0)
        self.assertIn("Reason: model_changed", body)
        self.assertIn("Missed: 2,048", body)
        self.assertIn("Reusable: 1,024", body)

    def test_diagnostic_without_reason_adds_no_detail_line(self):
        self.establish_cache_baseline()
        self.state.consume(
            event(
                "event_msg",
                {
                    "type": "response_metadata",
                    "prompt_cache_diagnostics": {
                        "type": "cache_miss",
                        "cache_missed_tokens": 2048,
                    },
                },
            )
        )
        _, body = self.token_count(0)
        self.assertEqual(len(body.splitlines()), 2)

    def test_resumed_rollout_reuses_logical_session_baseline(self):
        baselines = {}
        previous = CodexSessionState(Path("previous.jsonl"), baselines)
        previous.consume(
            event(
                "session_meta",
                {"id": "shared-session", "originator": "Codex Desktop"},
            )
        )
        previous.consume(
            event(
                "event_msg",
                {
                    "type": "token_count",
                    "info": {
                        "last_token_usage": {
                            "input_tokens": 180_000,
                            "cached_input_tokens": 179_000,
                            "cache_write_input_tokens": 0,
                        }
                    },
                },
            )
        )

        resumed = CodexSessionState(Path("resumed.jsonl"), baselines)
        resumed.consume(
            event(
                "session_meta",
                {"id": "shared-session", "originator": "Codex Desktop"},
            )
        )
        result = resumed.consume(
            event(
                "event_msg",
                {
                    "type": "token_count",
                    "info": {
                        "last_token_usage": {
                            "input_tokens": 192_000,
                            "cached_input_tokens": 0,
                            "cache_write_input_tokens": 0,
                        }
                    },
                },
            )
        )

        self.assertIsNotNone(result)
        self.assertEqual(result[0], "Oops! Cache miss!")


if __name__ == "__main__":
    unittest.main()
