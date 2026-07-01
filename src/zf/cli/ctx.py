"""zf ctx — agent-facing context pull(X15)。

"hook push 短 breadcrumb + CLI pull 详细上下文":聚合 Task Capsule
路径、动作分面 manifest、最近事件、dispatch 归属与期望下一事件。
只读;输出 human(默认)或 --json。
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from zf.core.events.log import EventLog
from zf.core.config.project_context import resolve_state_dir
from zf.core.task.store import TaskStore
from zf.runtime.task_context_manifest import read_task_context_manifest


def _latest_dispatch(events, task_id: str) -> dict:
    for event in reversed(events):
        if event.type == "task.dispatched" and event.task_id == task_id:
            payload = event.payload if isinstance(event.payload, dict) else {}
            return {
                "dispatch_id": str(payload.get("dispatch_id") or ""),
                "assignee": str(payload.get("assignee")
                                or payload.get("role") or ""),
                "ts": event.ts,
            }
    return {}


def _expected_next(task) -> list[str]:
    contract = getattr(task, "contract", None)
    events = list(getattr(contract, "completion_events", []) or [])
    if events:
        return events
    return ["<role publishes 的 success/failure 事件;见 briefing 完成协议>"]


def build_context(state_dir: Path, task_id: str, mode: str = "") -> dict:
    store = TaskStore(state_dir / "kanban.json")
    task = store.get(task_id)
    if task is None:
        return {"error": f"task {task_id!r} not found", "task_id": task_id}
    events = list(EventLog(state_dir / "events.jsonl").read_days(2))
    task_events = [e for e in events if e.task_id == task_id]
    dispatch = _latest_dispatch(events, task_id)
    briefing_dir = (
        state_dir / "briefings" / task_id / dispatch.get("dispatch_id", "")
    )
    manifest = read_task_context_manifest(briefing_dir) or {}
    contexts = manifest.get("contexts") or {}
    if mode:
        contexts = {mode: contexts.get(mode, [])}
    docs_dir = state_dir / "task_docs" / task_id
    return {
        "task_id": task_id,
        "title": getattr(task, "title", ""),
        "status": getattr(task, "status", ""),
        "capsule": {
            name: str(docs_dir / f"{name}.md")
            for name in ("source", "task", "progress", "evidence")
            if (docs_dir / f"{name}.md").exists()
        },
        "dispatch": dispatch,
        "contexts": contexts,
        "expected_next_events": _expected_next(task),
        "recent_events": [
            {"type": e.type, "ts": e.ts, "actor": e.actor}
            for e in task_events[-8:]
        ],
    }


def _render_human(ctx: dict) -> str:
    if ctx.get("error"):
        return f"error: {ctx['error']}"
    lines = [
        f"# ctx {ctx['task_id']} — {ctx.get('title', '')} [{ctx.get('status', '')}]",
        "",
    ]
    if ctx.get("dispatch"):
        d = ctx["dispatch"]
        lines.append(
            f"dispatch: {d.get('dispatch_id', '-')} → {d.get('assignee', '-')}"
        )
    if ctx.get("capsule"):
        lines.append("capsule:")
        lines += [f"  {k}: {v}" for k, v in ctx["capsule"].items()]
    for facet, entries in (ctx.get("contexts") or {}).items():
        if not entries:
            continue
        lines.append(f"{facet}:")
        for e in entries:
            req = "!" if e.get("required") else " "
            lines.append(f"  [{req}] {e.get('path', '')} — {e.get('reason', '')}")
    lines.append(
        "expected next: " + ", ".join(ctx.get("expected_next_events", []))
    )
    if ctx.get("recent_events"):
        lines.append("recent events:")
        lines += [
            f"  {e['ts']} {e['type']} ({e.get('actor')})"
            for e in ctx["recent_events"]
        ]
    return "\n".join(lines)


def cmd_ctx(args: argparse.Namespace) -> int:
    state_dir = resolve_state_dir(
        explicit_state_dir=getattr(args, "state_dir", None),
    )
    ctx = build_context(state_dir, args.task, mode=args.mode or "")
    if args.json:
        print(json.dumps(ctx, ensure_ascii=False, indent=2))
    else:
        print(_render_human(ctx))
    return 0 if not ctx.get("error") else 1


def register(subparsers) -> None:
    parser = subparsers.add_parser(
        "ctx", help="agent-facing context pull(task capsule/manifest/events)",
    )
    parser.add_argument("--task", required=True, help="Task ID")
    parser.add_argument(
        "--mode", choices=["implement", "check", "research", "closeout"],
        default="", help="只看某一动作分面",
    )
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--state-dir", default=None)
    parser.set_defaults(func=cmd_ctx)
