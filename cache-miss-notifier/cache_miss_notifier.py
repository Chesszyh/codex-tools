#!/usr/bin/env python3
"""Notify on Codex prompt-cache misses and large cache hits."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import selectors
import shutil
import subprocess
import sys
import time
from typing import Any, Callable


MISS_TITLE = "Oops! Cache miss!"
HIT_TITLE = "Cache hit!"
PRIME_ACTIVE_SECONDS = 48 * 60 * 60


def _number(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _find_cache_diagnostic(payload: dict[str, Any]) -> dict[str, Any] | None:
    candidates = [payload, payload.get("response"), payload.get("metadata")]
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        diagnostic = candidate.get("prompt_cache_diagnostics")
        if isinstance(diagnostic, dict):
            return diagnostic
        if candidate.get("type") == "cache_miss" and (
            "reason" in candidate or "cache_missed_tokens" in candidate
        ):
            return candidate
    return None


def _format_notification(
    title: str,
    source: str,
    model: str,
    input_tokens: int,
    cached_tokens: int,
    cache_write_tokens: int,
    diagnostic: dict[str, Any] | None,
) -> tuple[str, str]:
    uncached_tokens = max(0, input_tokens - cached_tokens - cache_write_tokens)
    body_lines = [
        f"{source} · {model}",
        f"Input {input_tokens:,} · cached {cached_tokens:,} · uncached {uncached_tokens:,}",
    ]
    if cache_write_tokens:
        body_lines.append(f"Cache write {cache_write_tokens:,}")

    if diagnostic is not None and diagnostic.get("type") == "cache_miss":
        reason = diagnostic.get("reason")
        if isinstance(reason, str) and reason:
            details = [f"Reason: {reason}"]
            missed = _number(diagnostic.get("cache_missed_tokens"))
            reusable = _number(diagnostic.get("comparison_reusable_tokens"))
            if missed is not None:
                details.append(f"Missed: {missed:,}")
            if reusable is not None:
                details.append(f"Reusable: {reusable:,}")
            body_lines.append(" · ".join(details))
    return title, "\n".join(body_lines)


class CacheBaseline:
    def __init__(self) -> None:
        self.request_seen = False
        self.cache_seen = False


class CodexSessionState:
    def __init__(
        self,
        path: Path,
        baselines: dict[str, CacheBaseline] | None = None,
    ) -> None:
        self.path = path
        self.offset = 0
        self.source = "Codex"
        self.model = "unknown model"
        self.context_window: int | None = None
        self.pending_diagnostic: dict[str, Any] | None = None
        self.baselines = baselines if baselines is not None else {}
        self.baseline = CacheBaseline()

    def prime(self) -> None:
        stat = self.path.stat()
        self.offset = stat.st_size
        if time.time() - stat.st_mtime > PRIME_ACTIVE_SECONDS:
            return
        with self.path.open("rb") as stream:
            for line in stream:
                self.consume(line.decode("utf-8", errors="replace"))

    def consume(self, line: str) -> tuple[str, str] | None:
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            return None

        if event.get("type") == "compacted":
            self.baseline.request_seen = False
            self.baseline.cache_seen = False
            self.pending_diagnostic = None
            return None

        payload = event.get("payload")
        if not isinstance(payload, dict):
            return None

        if event.get("type") == "session_meta":
            originator = payload.get("originator")
            if isinstance(originator, str) and originator:
                self.source = originator
            logical_id = payload.get("id")
            if isinstance(logical_id, str) and logical_id:
                existing = self.baselines.get(logical_id)
                if existing is None:
                    self.baselines[logical_id] = self.baseline
                else:
                    self.baseline = existing
            return None

        if event.get("type") == "turn_context":
            model = payload.get("model")
            if isinstance(model, str) and model:
                self.model = model
            return None

        if event.get("type") == "event_msg" and payload.get("type") == "task_started":
            context_window = _number(payload.get("model_context_window"))
            self.context_window = context_window if context_window and context_window > 0 else None
            return None

        diagnostic = _find_cache_diagnostic(payload)
        if diagnostic is not None:
            self.pending_diagnostic = diagnostic

        if event.get("type") != "event_msg" or payload.get("type") != "token_count":
            return None

        info = payload.get("info")
        usage = info.get("last_token_usage") if isinstance(info, dict) else None
        if not isinstance(usage, dict):
            return None

        input_tokens = _number(usage.get("input_tokens"))
        cached_tokens = _number(usage.get("cached_input_tokens"))
        cache_write_tokens = _number(usage.get("cache_write_input_tokens"))
        if input_tokens is None or cached_tokens is None or input_tokens <= 0:
            return None
        is_miss = (
            self.baseline.request_seen
            and self.baseline.cache_seen
            and cached_tokens == 0
        )
        is_large_hit = (
            self.context_window is not None
            and cached_tokens * 4 > self.context_window * 3
        )
        self.baseline.request_seen = True
        self.baseline.cache_seen = (
            self.baseline.cache_seen or cached_tokens > 0 or bool(cache_write_tokens)
        )
        diagnostic = self.pending_diagnostic
        self.pending_diagnostic = None
        if is_large_hit:
            return _format_notification(
                HIT_TITLE,
                self.source,
                self.model,
                input_tokens,
                cached_tokens,
                cache_write_tokens or 0,
                None,
            )
        if not is_miss:
            return None
        return _format_notification(
            MISS_TITLE,
            self.source,
            self.model,
            input_tokens,
            cached_tokens,
            cache_write_tokens or 0,
            diagnostic,
        )


class Watcher:
    def __init__(
        self,
        codex_sessions_dir: Path,
        notify: Callable[[str, str], None],
        verbose: bool = False,
    ) -> None:
        self.sessions_dir = codex_sessions_dir
        self.notify = notify
        self.verbose = verbose
        self.states: dict[Path, CodexSessionState] = {}
        self.baselines: dict[str, CacheBaseline] = {}

    def prime_existing(self) -> None:
        for path in sorted(self.sessions_dir.rglob("*.jsonl")):
            state = CodexSessionState(path, self.baselines)
            try:
                state.prime()
            except (OSError, UnicodeError):
                continue
            self.states[path] = state

    def read_updates(self, path: Path) -> None:
        if path.suffix != ".jsonl" or not path.is_file():
            return
        state = self.states.get(path)
        if state is None:
            state = CodexSessionState(path, self.baselines)
            self.states[path] = state
        try:
            size = path.stat().st_size
            if size < state.offset:
                state.offset = 0
            with path.open(encoding="utf-8") as stream:
                stream.seek(state.offset)
                for line in stream:
                    result = state.consume(line)
                    if result is not None:
                        self.notify(*result)
                state.offset = stream.tell()
        except (OSError, UnicodeError) as error:
            if self.verbose:
                print(f"Could not read {path}: {error}", file=sys.stderr)

    def run(self) -> None:
        self.sessions_dir.mkdir(parents=True, exist_ok=True)
        self.prime_existing()
        if sys.platform == "darwin":
            if self.verbose:
                print(f"Watching {self.sessions_dir}", file=sys.stderr)
            while True:
                for path in self.sessions_dir.rglob("*.jsonl"):
                    self.read_updates(path)
                time.sleep(2)

        command = [
            "inotifywait",
            "--monitor",
            "--recursive",
            "--quiet",
            "--event",
            "modify",
            "--event",
            "create",
            "--event",
            "moved_to",
            "--format",
            "%w%f",
            str(self.sessions_dir),
        ]
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        assert process.stdout is not None
        selector = selectors.DefaultSelector()
        selector.register(process.stdout, selectors.EVENT_READ)
        if self.verbose:
            print(f"Watching {self.sessions_dir}", file=sys.stderr)
        try:
            while process.poll() is None:
                for key, _ in selector.select(timeout=2):
                    changed = key.fileobj.readline().rstrip("\n")
                    if changed:
                        self.read_updates(Path(changed))
        except KeyboardInterrupt:
            pass
        finally:
            process.terminate()
            process.wait(timeout=5)


def desktop_notify(title: str, body: str) -> None:
    if sys.platform == "darwin":
        subprocess.run(
            [
                "osascript",
                "-e",
                "on run argv\n"
                "display notification (item 2 of argv) with title (item 1 of argv)\n"
                "end run",
                title,
                body,
            ],
            check=False,
        )
        return
    subprocess.run(
        ["notify-send", "--app-name=Codex Cache Watcher", title, body],
        check=False,
    )


def print_notify(title: str, body: str) -> None:
    print(f"{title}\n{body}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Notify on Codex prompt-cache misses and cache hits over 75% of the current context window."
    )
    parser.add_argument(
        "--sessions-dir",
        type=Path,
        default=Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")) / "sessions",
    )
    parser.add_argument("--dry-run", action="store_true", help="Print instead of notifying")
    parser.add_argument("--verbose", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    executables = ("osascript",) if sys.platform == "darwin" else ("inotifywait", "notify-send")
    for executable in executables:
        if not args.dry_run and shutil.which(executable) is None:
            print(f"Required command not found: {executable}", file=sys.stderr)
            return 2
    Watcher(
        codex_sessions_dir=args.sessions_dir.expanduser().resolve(),
        notify=print_notify if args.dry_run else desktop_notify,
        verbose=args.verbose,
    ).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
