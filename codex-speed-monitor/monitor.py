#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import signal
import sqlite3
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parent
COLORS = ["#2563eb", "#dc6b25", "#169b83", "#a855b5", "#c94768", "#64748b", "#b49419", "#0891b2"]
FIELDS = ["thread_id", "title", "model", "effort", "start_log_id", "started_at", "finished_at",
          "usage_at", "duration_seconds", "output_tokens", "reasoning_tokens", "non_reasoning_tokens",
          "input_tokens", "cached_input_tokens", "request_tps", "non_reasoning_tps"]
MODEL = re.compile(r"\bmodel=\"?([^\s\"}]+)")
EFFORT = re.compile(r"codex\.turn\.reasoning_effort=(\w+)")


def stamp(value: float) -> str:
    return datetime.fromtimestamp(value, timezone.utc).isoformat(timespec="microseconds")


def parse_stamp(value: str) -> float:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()


def read_db(path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


@dataclass
class Request:
    log_id: int
    started: float
    model: str
    effort: str
    ends: list[float] = field(default_factory=list)
    tool_running: int = 0
    completed: bool = False
    closed: bool = False


@dataclass
class Session:
    thread_id: str
    title: str
    path: Path | None
    model: str = "unknown"
    effort: str = "unknown"
    offset: int = 0
    seeded: bool = False
    total_output: int | None = None
    active: bool = False
    last_activity: float = 0
    turn_finished: float = 0
    requests: list[Request] = field(default_factory=list)
    pending: list[tuple[float, dict]] = field(default_factory=list)

    def log(self, log_id: int, ts: float, body: str) -> None:
        self.last_activity = max(self.last_activity, ts)
        if ': endpoint="/responses"' in body:
            model, effort = MODEL.search(body), EFFORT.search(body)
            self.requests.append(Request(log_id, ts, model[1] if model else self.model,
                                         effort[1] if effort else self.effort))
            return
        if not self.requests:
            return
        req = self.requests[-1]
        if "handle_output_item_done: Output item" in body or " ToolCall:" in body:
            req.ends.append(ts)
        if " ToolCall:" in body:
            req.tool_running += 1
        if 'event.name="codex.tool_call"' in body and "tool call completed" in body:
            req.tool_running = max(0, req.tool_running - 1)
        if "post sampling token usage" in body:
            req.closed = True

    def event(self, event: dict) -> None:
        ts, payload = parse_stamp(event["timestamp"]), event["payload"]
        if event["type"] == "turn_context":
            self.model = payload.get("model") or self.model
            self.effort = payload.get("effort") or self.effort
        if event["type"] != "event_msg":
            return
        kind = payload.get("type")
        if kind == "task_started":
            self.active, self.last_activity = True, ts
        elif kind in ("task_complete", "turn_aborted"):
            self.active, self.turn_finished, self.last_activity = False, ts, ts
        elif kind == "token_count" and payload.get("info"):
            info = payload["info"]
            total = info["total_token_usage"]["output_tokens"]
            # Rate-limit notifications can repeat the previous usage unchanged.
            if total != self.total_output and self.seeded:
                self.pending.append((ts, info["last_token_usage"]))
            self.total_output, self.seeded = total, True

    def read_tail(self, errors: list[str]) -> None:
        if self.path is None:
            return
        try:
            with self.path.open("rb") as stream:
                if self.path.stat().st_size < self.offset:
                    self.offset, self.total_output = 0, None
                    self.seeded = True
                stream.seek(self.offset)
                while True:
                    start = stream.tell()
                    line = stream.readline()
                    if not line or not line.endswith(b"\n"):
                        self.offset = start
                        break
                    self.offset = stream.tell()
                    try:
                        self.event(json.loads(line))
                    except (ValueError, KeyError, TypeError) as exc:
                        errors.append(f"无法解析会话 {self.thread_id} 的第 {start} 字节记录：{exc}")
        except OSError as exc:
            errors.append(f"无法读取 {self.path}：{exc}")

    def collect(self, cutoff: float) -> list[dict]:
        result, waiting = [], []
        for usage_at, usage in self.pending:
            if usage_at < cutoff:
                continue
            candidates = [r for r in self.requests if r.started <= usage_at]
            if not candidates:
                waiting.append((usage_at, usage))
                continue
            req = candidates[-1]
            ends = [t for t in req.ends if t <= usage_at]
            # SQLite logs may flush after the rollout usage; wait for the sampling boundary.
            if not ends or not req.closed:
                waiting.append((usage_at, usage))
                continue
            if req.completed:
                waiting.append((usage_at, usage))
                continue
            finished = max(ends)
            duration = finished - req.started
            if duration <= 0:
                continue
            output = usage["output_tokens"]
            reasoning = usage.get("reasoning_output_tokens", 0)
            result.append(dict(thread_id=self.thread_id, title=self.title, model=req.model,
                               effort=req.effort, start_log_id=req.log_id, started_at=stamp(req.started),
                               finished_at=stamp(finished), usage_at=stamp(usage_at),
                               duration_seconds=duration, output_tokens=output, reasoning_tokens=reasoning,
                               non_reasoning_tokens=output - reasoning, input_tokens=usage.get("input_tokens", 0),
                               cached_input_tokens=usage.get("cached_input_tokens", 0),
                               request_tps=output / duration, non_reasoning_tps=(output - reasoning) / duration))
            req.completed = True
        self.pending = waiting
        return result

    def status(self) -> tuple[str, float | None]:
        if self.requests:
            req = self.requests[-1]
            if not req.completed and req.started > self.turn_finished:
                return ("工具执行" if req.tool_running else "生成中", req.started)
        return ("等待" if self.active else "空闲", None)


class Store:
    def __init__(self, directory: Path):
        directory.mkdir(parents=True, exist_ok=True)
        self.directory = directory
        self.con = sqlite3.connect(directory / "metrics.sqlite3")
        self.con.row_factory = sqlite3.Row
        self.con.execute("""CREATE TABLE IF NOT EXISTS requests (
            thread_id TEXT, title TEXT, model TEXT, effort TEXT, start_log_id INTEGER,
            started_at TEXT, finished_at TEXT, usage_at TEXT, duration_seconds REAL,
            output_tokens INTEGER, reasoning_tokens INTEGER, non_reasoning_tokens INTEGER,
            input_tokens INTEGER, cached_input_tokens INTEGER, request_tps REAL,
            non_reasoning_tps REAL, PRIMARY KEY(thread_id, start_log_id))""")
        self.con.execute("""CREATE TABLE IF NOT EXISTS observations (
            observed_at TEXT, thread_id TEXT, status TEXT, request_started_at TEXT,
            elapsed_seconds REAL, last_request_tps REAL, recent_request_tps REAL,
            window_tps REAL)""")
        self.con.commit()

    def add(self, rows: list[dict]) -> int:
        count = self.con.total_changes
        assignments = ','.join(f'{k}=excluded.{k}' for k in FIELDS if k not in ('thread_id','start_log_id'))
        self.con.executemany(f"""INSERT INTO requests ({','.join(FIELDS)}) VALUES ({','.join('?' for _ in FIELDS)})
                             ON CONFLICT(thread_id,start_log_id) DO UPDATE SET {assignments}
                             WHERE requests.duration_seconds<>excluded.duration_seconds
                                OR requests.output_tokens<>excluded.output_tokens""",
                             [tuple(row[k] for k in FIELDS) for row in rows])
        self.con.commit()
        return self.con.total_changes - count

    def history(self) -> list[dict]:
        return [dict(r) for r in self.con.execute("SELECT * FROM requests ORDER BY finished_at")]

    def refresh_titles(self, sessions: dict[str, Session]) -> None:
        self.con.executemany("UPDATE requests SET title=? WHERE thread_id=? AND title<>?",
                             [(s.title, s.thread_id, s.title) for s in sessions.values()])
        self.con.commit()

    def observe(self, state: dict) -> None:
        fields = ["observed_at", "thread_id", "status", "request_started_at", "elapsed_seconds",
                  "last_request_tps", "recent_request_tps", "window_tps"]
        rows = []
        for session in state["sessions"]:
            rows.append([stamp(state["now"]), session["thread_id"], session["status"],
                         stamp(session["request_started"]) if session["request_started"] else None,
                         session["elapsed_seconds"], session["last_request_tps"],
                         session["recent_request_tps"], session["window_tps"]])
        self.con.executemany("INSERT INTO observations VALUES (?,?,?,?,?,?,?,?)", rows)
        self.con.commit()
        path = self.directory / "observations.csv"
        header = not path.exists()
        with path.open("a", newline="", encoding="utf-8") as stream:
            writer = csv.writer(stream)
            if header:
                writer.writerow(fields)
            writer.writerows(rows)

    def export(self, state: dict) -> None:
        rows = [dict(r) for r in self.con.execute("SELECT * FROM requests ORDER BY finished_at")]
        with (self.directory / "requests.csv").open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=FIELDS)
            writer.writeheader()
            writer.writerows(rows)
        (self.directory / "latest.svg").write_text(chart_svg(state), encoding="utf-8")
        (self.directory / "snapshot.html").write_text(dashboard(state, saved=True), encoding="utf-8")
        (self.directory / "latest.json").write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


class Monitor:
    def __init__(self, home: Path, store: Store, lookback: float, interval: float, tz: str):
        self.home, self.store = home, store
        self.cutoff = time.time() - lookback * 60
        self.interval, self.tz = interval, tz
        self.state_db = read_db(home / "state_5.sqlite")
        self.logs_db = read_db(home / "logs_2.sqlite")
        self.sessions: dict[str, Session] = {}
        row = self.logs_db.execute("SELECT min(id) FROM logs WHERE ts >= ?", (int(self.cutoff),)).fetchone()
        self.cursor = (row[0] - 1) if row[0] else self.logs_db.execute("SELECT coalesce(max(id),0) FROM logs").fetchone()[0]
        self.started = time.time()

    def discover(self) -> None:
        for row in self.state_db.execute("""SELECT id, coalesce(nullif(name,''),agent_nickname,nullif(title,''),id) AS title,
                                              rollout_path, model, reasoning_effort FROM threads
                                              WHERE archived = 0 AND updated_at >= ?""", (int(self.cutoff),)):
            if row["id"] in self.sessions and self.sessions[row["id"]].path is not None:
                self.sessions[row["id"]].title = row["title"]
                continue
            path = Path(row["rollout_path"])
            if not path.exists():
                continue
            session = Session(row["id"], row["title"], path, row["model"] or "unknown", row["reasoning_effort"] or "unknown", seeded=True)
            self.sessions[session.thread_id] = session

    def poll(self) -> dict:
        errors = []
        self.discover()
        for row in self.logs_db.execute("""SELECT id,ts,ts_nanos,thread_id,feedback_log_body FROM logs
                                           WHERE id > ? ORDER BY id""", (self.cursor,)):
            self.cursor = row["id"]
            session = self.sessions.get(row["thread_id"])
            if session:
                session.log(row["id"], row["ts"] + row["ts_nanos"] / 1e9, row["feedback_log_body"] or "")
        new_rows = []
        for session in self.sessions.values():
            session.read_tail(errors)
            new_rows.extend(session.collect(self.cutoff))
        added = self.store.add(new_rows)
        self.store.refresh_titles(self.sessions)
        now = time.time()
        history = self.store.history()
        by_thread: dict[str, list[dict]] = {}
        for row in history:
            by_thread.setdefault(row["thread_id"], []).append(row)
        summaries = []
        for thread_id, rows in by_thread.items():
            if thread_id not in self.sessions:
                last = rows[-1]
                self.sessions[thread_id] = Session(thread_id, last['title'], None, last['model'], last['effort'])
        for thread_id, session in self.sessions.items():
            if not session.requests and thread_id not in by_thread:
                continue
            rows = by_thread.get(thread_id, [])
            recent = [r for r in rows if r['model'] == session.model and r['effort'] == session.effort][-5:]
            status, started = session.status()
            window = sum(row["output_tokens"] for row in rows if parse_stamp(row["usage_at"]) >= now - 60) / 60
            last = recent[-1] if recent else None
            summaries.append(dict(thread_id=thread_id, title=session.title, model=session.model, effort=session.effort,
                                  status=status, request_started=started, elapsed_seconds=now-started if started else None,
                                  last_request_tps=last["request_tps"] if last else None,
                                  recent_request_tps=sum(r["output_tokens"] for r in recent) / sum(r["duration_seconds"] for r in recent) if recent else None,
                                  window_tps=window, request_count=len(rows),
                                  last_sample_at=last["finished_at"] if last else None))
        state = dict(now=now, started=self.started, interval=self.interval, timezone=self.tz,
                     output_dir=str(self.store.directory), sessions=summaries, requests=history, errors=errors,
                     added=added, metric="request_average", window_seconds=60)
        self.store.observe(state)
        return state


def chart_svg(state: dict) -> str:
    tz = ZoneInfo(state["timezone"])
    rows = state["requests"]
    threads = list(dict.fromkeys(r["thread_id"] for r in rows))
    width, height = 1200, 420 + 28 * len(threads)
    left, right, top, bottom = 70, 1170, 80, 350
    xs = [parse_stamp(r["finished_at"]) for r in rows]
    xmin, xmax = (min(xs), max(xs)) if xs else (state["now"]-60, state["now"])
    if xmax-xmin < 1:
        xmin -= 30
        xmax += 30
    ymax = max([r["request_tps"] for r in rows] + [10]) * 1.15
    x = lambda v: left + (v-xmin)/(xmax-xmin)*(right-left)
    y = lambda v: bottom - v/ymax*(bottom-top)
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
             '<rect width="100%" height="100%" fill="#f8fafc"/>',
             '<g font-family="sans-serif" fill="#0f172a">',
             '<text x="28" y="32" font-size="21">Codex · 会话输出速度</text>',
             '<text x="28" y="55" font-size="13" fill="#64748b">请求平均速度 · 含推理 token · token/s</text>']
    for i in range(6):
        value = ymax*i/5
        parts.extend([f'<path d="M {left} {y(value):.1f} H {right}" stroke="#e2e8f0"/>',
                      f'<text x="{left-10}" y="{y(value)+4:.1f}" font-size="12" text-anchor="end">{value:.0f}</text>'])
        tick = xmin + (xmax-xmin)*i/5
        label = datetime.fromtimestamp(tick, tz).strftime("%H:%M:%S")
        parts.append(f'<text x="{x(tick):.1f}" y="{bottom+24}" font-size="12" text-anchor="middle" fill="#64748b">{label}</text>')
    for i, tid in enumerate(threads):
        points = [r for r in rows if r["thread_id"] == tid]
        color = COLORS[i % len(COLORS)]
        xy = " ".join(f'{x(parse_stamp(r["finished_at"])):.1f},{y(r["request_tps"]):.1f}' for r in points)
        parts.append(f'<polyline points="{xy}" fill="none" stroke="{color}" stroke-width="2"/>')
        for r in points:
            parts.append(f'<circle cx="{x(parse_stamp(r["finished_at"])):.1f}" cy="{y(r["request_tps"]):.1f}" r="3" fill="{color}"/>')
        variants = ', '.join(dict.fromkeys(f'{p["model"]} / {p["effort"]}' for p in points))
        label = f'{points[-1]["title"]} · {variants}'
        ly = 406 + i*28
        parts.append(f'<circle cx="30" cy="{ly-4}" r="5" fill="{color}"/><text x="45" y="{ly}" font-size="13">{escape(label)}</text>')
    if not rows:
        parts.append('<text x="600" y="210" text-anchor="middle" fill="#64748b">等待首个请求完成</text>')
    parts.append('</g></svg>')
    return "".join(parts)


def dashboard(state: dict, saved: bool = False) -> str:
    data = json.dumps(state, ensure_ascii=False).replace("<", "\\u003c")
    return (ROOT / "dashboard.html").read_text(encoding="utf-8").replace("__INITIAL_STATE__", data).replace("__SAVED__", str(saved).lower())


class StateServer(ThreadingHTTPServer):
    def __init__(self, port: int, state: dict):
        super().__init__(("127.0.0.1", port), Handler)
        self.state = state
        self.lock = threading.Lock()


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        with self.server.lock:
            state = self.server.state
        path = urlsplit(self.path).path
        if path == "/":
            payload, content_type = dashboard(state).encode(), "text/html; charset=utf-8"
        elif path == "/api/state":
            payload, content_type = json.dumps(state, ensure_ascii=False).encode(), "application/json; charset=utf-8"
        elif path in ("/requests.csv", "/snapshot.html", "/chart.svg"):
            filename, content_type = {"/requests.csv": ("requests.csv", "text/csv; charset=utf-8"),
                                      "/snapshot.html": ("snapshot.html", "text/html; charset=utf-8"),
                                      "/chart.svg": ("latest.svg", "image/svg+xml")}[path]
            payload = (Path(state["output_dir"]) / filename).read_bytes()
        else:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *_args) -> None:
        pass


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="监测本机 Codex 会话的请求平均输出速度，保存数据和图表。")
    p.add_argument("--codex-home", type=Path, default=Path.home()/".codex", help="Codex 日志目录")
    p.add_argument("--output", type=Path, default=ROOT/"data", help="数据和图表保存目录")
    p.add_argument("--interval", type=float, default=2, help="采样间隔秒数（默认 2）")
    p.add_argument("--lookback-minutes", type=float, default=120, help="启动时回填最近多少分钟（默认 120）")
    p.add_argument("--timezone", default="Asia/Shanghai", help="图表显示时区（默认 Asia/Shanghai）")
    p.add_argument("--port", type=int, default=8766, help="本地网页端口（默认 8766）")
    p.add_argument("--once", action="store_true", help="读取一次并导出，不启动网页")
    p.add_argument("--duration", type=float, help="运行多少秒后停止；省略时持续运行")
    p.add_argument("--no-server", action="store_true", help="只记录和导出，不启动网页")
    return p


def main() -> int:
    args = parser().parse_args()
    if args.interval <= 0 or args.lookback_minutes <= 0 or (args.duration is not None and args.duration <= 0):
        parser().error("interval、lookback-minutes 和 duration 必须大于 0")
    stop = threading.Event()
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    server = None
    pid_path = None
    try:
        ZoneInfo(args.timezone)
        store = Store(args.output.expanduser().resolve())
        monitor = Monitor(args.codex_home.expanduser().resolve(), store, args.lookback_minutes, args.interval, args.timezone)
        state = monitor.poll()
        store.export(state)
        print(f"记录目录：{store.directory}", flush=True)
        print(f"已记录 {len(state['requests'])} 个请求、{len(state['sessions'])} 个会话。", flush=True)
        for message in state['errors']:
            print(message, file=sys.stderr)
        if args.once:
            return 0
        if not args.no_server:
            server = StateServer(args.port, state)
            threading.Thread(target=server.serve_forever, daemon=True).start()
            print(f"实时图表：http://127.0.0.1:{server.server_port}/", flush=True)
        pid_path = store.directory / "monitor.pid"
        pid_path.write_text(str(os.getpid()) + "\n", encoding="utf-8")
        deadline = time.monotonic() + args.duration if args.duration else None
        last_export = time.monotonic()
        while not stop.wait(args.interval):
            state = monitor.poll()
            if state["added"] or time.monotonic()-last_export >= 15:
                store.export(state)
                last_export = time.monotonic()
            if server:
                with server.lock:
                    server.state = state
            if state["added"]:
                print(f"更新 {state['added']} 个请求；累计 {len(state['requests'])} 个请求。", flush=True)
            for message in state["errors"]:
                print(message, file=sys.stderr)
            if deadline and time.monotonic() >= deadline:
                break
        store.export(state)
        return 0
    except (OSError, sqlite3.Error, ValueError, KeyError) as exc:
        print(f"监测失败：{exc}", file=sys.stderr)
        return 1
    finally:
        if server:
            server.shutdown()
            server.server_close()
        if pid_path:
            pid_path.unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())
