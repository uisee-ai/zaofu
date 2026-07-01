"""zf watch — structured event tail with role/type filtering.

Replaces `zf attach` for stream-json roles where there is no terminal to
take over. Reads .zf/events.jsonl, optionally filters, pretty-prints
agent.tool.use / agent.text / agent.usage so a human can follow what an
agent is doing in real time.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from zf.core.config.project_context import resolve_project_context
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent


def register(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("watch", help="Tail .zf/events.jsonl with filtering")
    parser.add_argument("--role", default=None, help="Filter by actor (role name)")
    parser.add_argument("--type", default=None, help="Filter by event type (exact match)")
    parser.add_argument("--task", default=None, help="Filter by task id")
    parser.add_argument("--last", type=int, default=20, help="Show last N matching events")
    parser.add_argument("--follow", "-f", action="store_true", help="Tail mode: print new events as they arrive")
    parser.add_argument(
        "--state-dir",
        default=None,
        help="Runtime state dir (default: project.state_dir from zf.yaml)",
    )
    parser.set_defaults(func=run)


def run(args: argparse.Namespace) -> int:
    state_dir = resolve_project_context(
        explicit_state_dir=getattr(args, "state_dir", None),
    ).state_dir
    log_path = state_dir / "events.jsonl"

    if not log_path.exists():
        print(f"No events.jsonl found at {log_path}", file=sys.stderr)
        return 0

    log = EventLog(log_path)
    events = log.read_all()
    filtered = _filter(events, role=args.role, type_=args.type, task=args.task)
    for e in filtered[-args.last:]:
        print(_render(e))

    if args.follow:
        offset = log.current_offset()
        try:
            while True:
                time.sleep(0.5)
                new_events, offset = log.read_from_offset(offset)
                for e in _filter(new_events, role=args.role, type_=args.type, task=args.task):
                    print(_render(e), flush=True)
        except KeyboardInterrupt:
            return 0
    return 0


def _filter(events: list[ZfEvent], *, role: str | None, type_: str | None, task: str | None) -> list[ZfEvent]:
    out = events
    if role is not None:
        out = [e for e in out if e.actor == role]
    if type_ is not None:
        out = [e for e in out if e.type == type_]
    if task is not None:
        out = [e for e in out if e.task_id == task]
    return out


def _render(e: ZfEvent) -> str:
    base = (
        f"{e.ts}  {e.type:<24}  actor=`{e.actor or '?'}`  task=`{e.task_id or '?'}`"
    )
    detail = _payload_detail(e)
    if detail:
        return f"{base}\n    {detail}"
    return base


def _payload_detail(e: ZfEvent) -> str:
    if e.type == "agent.tool.use":
        tool = e.payload.get("tool", "?")
        inp = e.payload.get("input", {})
        return f"[tool: {tool}] {_short_json(inp)}"
    if e.type == "agent.tool.result":
        is_err = e.payload.get("is_error", False)
        marker = "ERR" if is_err else "ok"
        content = _short_json(e.payload.get("content", ""))
        return f"[result: {marker}] {content}"
    if e.type == "agent.text":
        text = e.payload.get("text", "")
        return _truncate(text, 200)
    if e.type == "agent.usage":
        usage = e.payload.get("usage", {})
        cost = e.payload.get("total_cost_usd", 0.0)
        return f"[usage] cost=${cost:.4f} {_short_json(usage)}"
    if e.payload:
        return _short_json(e.payload)
    return ""


def _short_json(obj) -> str:
    try:
        return _truncate(json.dumps(obj, ensure_ascii=False), 200)
    except (TypeError, ValueError):
        return _truncate(str(obj), 200)


def _truncate(text: str, n: int) -> str:
    text = str(text).replace("\n", " ")
    return text if len(text) <= n else text[: n - 1] + "…"
