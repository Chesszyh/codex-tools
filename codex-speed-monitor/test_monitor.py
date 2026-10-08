import csv
import json
import tempfile
import unittest
from pathlib import Path

from monitor import Session, Store, chart_svg, stamp


START = 'turn{model=gpt-6.1-sol codex.turn.reasoning_effort=medium}: endpoint="/responses"'
ITEM_DONE = 'handle_output_item_done: Output item item_type="reasoning"'
TOOL = 'handle_output_item_done: ToolCall: exec thread_id=abc'
BOUNDARY = 'post sampling token usage'


def usage(ts, total, output=100, reasoning=20):
    return dict(timestamp=stamp(ts), type="event_msg", payload=dict(type="token_count", info=dict(
        total_token_usage=dict(output_tokens=total),
        last_token_usage=dict(output_tokens=output, reasoning_output_tokens=reasoning,
                             input_tokens=500, cached_input_tokens=400))))


class MeasurementTests(unittest.TestCase):
    def session(self):
        return Session("abc", "会话", Path(), seeded=True)

    def test_tool_generation_ends_after_reasoning_and_excludes_tool_execution(self):
        session = self.session()
        session.log(1, 100, START)
        session.log(2, 102, ITEM_DONE)
        session.log(3, 110, TOOL)
        session.log(4, 140, 'tool call completed event.name="codex.tool_call"')
        session.log(5, 140, BOUNDARY)
        session.event(usage(140, 100))
        row = session.collect(0)[0]
        self.assertEqual(row["duration_seconds"], 10)
        self.assertEqual(row["request_tps"], 10)
        self.assertEqual(row["non_reasoning_tps"], 8)
        self.assertEqual(row["finished_at"], stamp(110))

    def test_repeated_usage_notifications_do_not_become_new_requests(self):
        session = self.session()
        session.log(1, 100, START)
        session.log(2, 110, ITEM_DONE)
        session.log(3, 111, BOUNDARY)
        session.event(usage(111, 100))
        self.assertEqual(len(session.collect(0)), 1)
        session.log(3, 115, START)
        session.log(4, 125, ITEM_DONE)
        session.log(5, 126, BOUNDARY)
        session.event(usage(126, 100))
        self.assertEqual(session.collect(0), [])
        session.event(usage(130, 200))
        self.assertEqual(session.collect(0)[0]["start_log_id"], 3)

    def test_each_request_retains_its_model_and_effort(self):
        session = self.session()
        session.log(1, 100, START)
        session.model = "gpt-6-astra"
        session.effort = "xhigh"
        session.log(2, 110, ITEM_DONE)
        session.log(3, 111, BOUNDARY)
        session.event(usage(111, 100))
        row = session.collect(0)[0]
        self.assertEqual((row["model"], row["effort"]), ("gpt-6.1-sol", "medium"))

    def test_missing_start_or_end_never_produces_a_speed(self):
        session = self.session()
        session.event(usage(111, 100))
        self.assertEqual(session.collect(0), [])
        session.log(1, 115, START)
        session.event(usage(130, 200))
        self.assertEqual(session.collect(0), [])
        session.log(2, 125, ITEM_DONE)
        session.log(3, 130, BOUNDARY)
        self.assertEqual(len(session.collect(0)), 1)

    def test_partial_jsonl_is_read_after_the_line_finishes(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/"rollout.jsonl"
            encoded = json.dumps(usage(111, 100)).encode()
            path.write_bytes(encoded[:20])
            session = Session("abc", "会话", path, seeded=True)
            session.log(1, 100, START)
            session.log(2, 110, ITEM_DONE)
            session.log(3, 111, BOUNDARY)
            errors = []
            session.read_tail(errors)
            self.assertEqual(session.offset, 0)
            self.assertEqual(session.collect(0), [])
            with path.open("ab") as stream:
                stream.write(encoded[20:] + b"\n")
            session.read_tail(errors)
            self.assertEqual(len(session.collect(0)), 1)
            self.assertEqual(errors, [])

    def test_tail_without_previous_usage_seeds_before_counting(self):
        session = self.session()
        session.seeded = False
        session.event(usage(101, 100))
        self.assertEqual(session.pending, [])
        session.log(1, 110, START)
        session.log(2, 120, ITEM_DONE)
        session.log(3, 121, BOUNDARY)
        session.event(usage(121, 200))
        self.assertEqual(len(session.collect(0)), 1)

    def test_active_request_and_tool_status_do_not_report_zero_speed(self):
        session = self.session()
        session.log(1, 100, START)
        self.assertEqual(session.status(), ("生成中", 100))
        session.log(2, 110, TOOL)
        self.assertEqual(session.status(), ("工具执行", 100))
        session.event(dict(timestamp=stamp(120), type="event_msg", payload=dict(type="turn_aborted")))
        self.assertEqual(session.status(), ("空闲", None))


class PersistenceTests(unittest.TestCase):
    def test_restart_deduplicates_and_exports_offline_graph_and_csv(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)
            session = Session("abc", "标题 </script>", Path(), seeded=True)
            session.log(1, 100, START)
            session.log(2, 110, ITEM_DONE)
            session.log(3, 111, BOUNDARY)
            session.event(usage(111, 100))
            row = session.collect(0)[0]
            store = Store(path)
            self.assertEqual(store.add([row]), 1)
            store.con.close()

            store = Store(path)
            self.assertEqual(store.add([row]), 0)
            state = dict(now=120, requests=store.history(), sessions=[], timezone="Asia/Shanghai",
                         output_dir=str(path), interval=2, errors=[])
            store.export(state)
            with (path/"requests.csv").open() as stream:
                self.assertEqual(len(list(csv.DictReader(stream))), 1)
            self.assertIn('<polyline', (path/"latest.svg").read_text())
            html = (path/"snapshot.html").read_text()
            self.assertNotIn('__INITIAL_STATE__', html)
            self.assertIn('\\u003c/script>', html)
            self.assertEqual(len(store.history()), 1)
            store.con.close()

    def test_usage_waits_for_delayed_log_flush(self):
        session = Session('abc', '会话', None, seeded=True)
        session.log(1, 100, START)
        session.log(2, 102, ITEM_DONE)
        session.event(usage(120, 100))
        self.assertEqual(session.collect(0), [])
        session.log(3, 110, TOOL)
        session.log(4, 120, BOUNDARY)
        self.assertEqual(session.collect(0)[0]['request_tps'], 10)

    def test_empty_chart_is_valid_svg(self):
        from xml.etree import ElementTree
        svg = chart_svg(dict(now=120, requests=[], timezone="Asia/Shanghai"))
        self.assertTrue(ElementTree.fromstring(svg).tag.endswith('svg'))


if __name__ == "__main__":
    unittest.main()
