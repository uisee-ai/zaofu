"""zf emit / zf events — event append and query commands."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any
from dataclasses import asdict

from zf.core.config.loader import ConfigError
from zf.core.config.project_context import resolve_project_context
from zf.core.events.factory import EventSigningConfigError, event_log_from_project
from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter

_REVISION_KEYS = (
    "source_revision",
    "contract_revision",
    "capsule_revision",
)

_COMPLETION_REVISION_EVENTS = frozenset({
    "arch.proposal.done",
    "design.critique.done",
    "dev.build.done",
    "task.done.evidence",
    "review.approved",
    "test.passed",
    "judge.passed",
})


def register(subparsers: argparse._SubParsersAction) -> None:
    # zf emit
    emit_parser = subparsers.add_parser("emit", help="Emit an event")
    emit_parser.add_argument("type", help="Event type (e.g. dev.build.done)")
    emit_parser.add_argument("--payload", type=str, default=None, help="JSON payload")
    emit_parser.add_argument(
        "--payload-file",
        type=Path,
        default=None,
        help="Path to a JSON payload file; use '-' to read JSON from stdin",
    )
    emit_parser.add_argument("--task", type=str, default=None, help="Task ID")
    emit_parser.add_argument("--actor", type=str, default=None, help="Actor name")
    emit_parser.add_argument(
        "--dispatch-id",
        type=str,
        default=None,
        help="Dispatch token from the task briefing (strict harness presets)",
    )
    emit_parser.add_argument(
        "--state-dir",
        type=str,
        default=None,
        help="Path to runtime state dir (default: project.state_dir from zf.yaml, else .zf)",
    )
    emit_parser.set_defaults(func=run_emit)

    # zf events  (and zf events trace <id>)
    events_parser = subparsers.add_parser("events", help="Query events")
    events_sub = events_parser.add_subparsers(dest="events_command")
    events_parser.add_argument("--type", type=str, default=None, help="Filter by type")
    events_parser.add_argument("--last", type=int, default=None, help="Show last N events")
    events_parser.add_argument("--json", action="store_true", help="Wrap output in zf.cli.result.v1")
    events_parser.add_argument(
        "--state-dir",
        type=str,
        default=None,
        help="Path to runtime state dir (default: project.state_dir from zf.yaml, else .zf)",
    )
    events_parser.set_defaults(func=run_events)

    trace_parser = events_sub.add_parser(
        "trace",
        help="Show the causation chain of an event",
    )
    trace_parser.add_argument("event_id", help="Event ID to trace (e.g. evt-abc123)")
    trace_parser.set_defaults(func=run_trace)


def run_emit(args: argparse.Namespace) -> int:
    try:
        context = resolve_project_context(
            explicit_state_dir=getattr(args, "state_dir", None),
        )
    except ConfigError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    state_dir = context.state_dir
    if not state_dir.exists():
        print("Error: not initialized. Run 'zf init' first.", file=sys.stderr)
        return 1

    payload = {}
    payload_file = getattr(args, "payload_file", None)
    if args.payload and payload_file is not None:
        print("Error: use only one of --payload or --payload-file", file=sys.stderr)
        return 1
    if payload_file is not None:
        try:
            if str(payload_file) == "-":
                raw_payload = sys.stdin.read()
            else:
                raw_payload = payload_file.read_text(encoding="utf-8")
            payload = json.loads(raw_payload)
        except OSError as e:
            print(f"Error: could not read --payload-file: {e}", file=sys.stderr)
            return 1
        except json.JSONDecodeError:
            print("Error: --payload-file must contain valid JSON", file=sys.stderr)
            return 1
    elif args.payload:
        try:
            payload = json.loads(args.payload)
        except json.JSONDecodeError:
            print("Error: --payload must be valid JSON", file=sys.stderr)
            return 1
    if not isinstance(payload, dict):
        print("Error: payload must be a JSON object", file=sys.stderr)
        return 1
    if getattr(args, "dispatch_id", None):
        payload["dispatch_id"] = args.dispatch_id
    if args.task:
        _autofill_completion_revisions(
            payload,
            state_dir=state_dir,
            event_type=args.type,
            task_id=args.task,
            dispatch_id=str(payload.get("dispatch_id") or ""),
        )

    try:
        event_log = event_log_from_project(state_dir, config=context.config)
    except EventSigningConfigError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    # TR-EVENT-SCHEMA-LOCK-001 step 2/3 (doc 42 §11.3 A): zf emit goes
    # through the same schema gate as Orchestrator-side appends. Without
    # this, the worker-facing emit channel (the natural place for bad
    # payloads to arrive) would silently bypass validation.
    #
    # context.config may be None when zf emit is invoked against a state
    # dir with no zf.yaml (test fixtures, ad-hoc runs); in that case no
    # registry / no mode → behave as schema_mode=disabled.
    from zf.core.verification.event_schema import EventSchemaRegistry
    if context.config is not None:
        _schema_registry = EventSchemaRegistry.from_config(context.config)
        _schema_mode = getattr(
            getattr(context.config, "verification", None),
            "event_schema",
            None,
        )
        _schema_mode = getattr(_schema_mode, "mode", "disabled")
    else:
        _schema_registry = EventSchemaRegistry()
        _schema_mode = "disabled"
    event_writer = EventWriter(
        event_log,
        schema_registry=_schema_registry if _schema_registry.rule_count() else None,
        schema_mode=_schema_mode,
        # 1405:zf emit 是 agent↔kernel ABI 的 worker 侧 —— 自报事件
        # 一律标 worker(kernel 自有 in-process writer,不走 CLI)。
        default_origin="worker",
    )
    if _terminal_run_quiesces_emit(args.type, event_log):
        event_log.close()
        print(f"Suppressed: {args.type} after terminal run")
        return 0

    # G-EVT-1: auto-fill causation_id by looking up the most recent event
    # with the same task_id. Forms a per-task chain without requiring
    # callers to track it manually. Events without a task_id don't get
    # forced into any chain.
    causation_id: str | None = None
    if args.task:
        for prev in reversed(event_log.read_all()):
            if prev.task_id == args.task:
                causation_id = prev.id
                break

    event = ZfEvent(
        type=args.type,
        actor=args.actor,
        task_id=args.task,
        payload=payload,
        causation_id=causation_id,
    )

    written = event_writer.append(event)
    event_log.close()
    _apply_emit_side_effect(state_dir, written)
    print(f"Emitted: {written.type} ({written.id})")
    if written.type == "discriminator.failed" and written.id != event.id:
        print(
            f"Blocked: {event.type} ({event.id}) violated the event schema",
            file=sys.stderr,
        )
        return 2
    return 0


def _terminal_run_quiesces_emit(event_type: str, event_log: Any) -> bool:
    if event_type != "run.manager.agent.observation":
        return False
    try:
        from zf.autoresearch.failure_signals import completed_run_quiesced

        return completed_run_quiesced(event_log.read_all())
    except Exception:
        return False


def _autofill_completion_revisions(
    payload: dict[str, Any],
    *,
    state_dir: Path,
    event_type: str,
    task_id: str,
    dispatch_id: str,
) -> None:
    """Copy authoritative task capsule revisions onto worker completions.

    The revision gate stays strict in the runtime reactor. This helper only
    normalizes the worker-facing `zf emit` command for the currently active
    dispatch so copy/paste or provider hallucination of revision ids does not
    turn a valid completion into avoidable rework.
    """
    if event_type not in _COMPLETION_REVISION_EVENTS:
        return
    if not task_id or not dispatch_id:
        return
    try:
        from zf.core.task.store import TaskStore

        task = TaskStore(state_dir / "kanban.json").get(task_id)
    except Exception:
        return
    if task is None:
        return
    if str(getattr(task, "active_dispatch_id", "") or "") != dispatch_id:
        return
    contract = getattr(task, "contract", None)
    if contract is None:
        return
    expected = {
        key: str(getattr(contract, key, "") or "")
        for key in _REVISION_KEYS
    }
    expected = {key: value for key, value in expected.items() if value}
    if not expected:
        return

    original: dict[str, str] = {}
    changed: list[str] = []
    for key, expected_value in expected.items():
        current = str(payload.get(key) or "")
        if current == expected_value:
            continue
        original[key] = current
        payload[key] = expected_value
        changed.append(key)

    if not changed:
        return
    payload["revision_autofill"] = {
        "source": "zf_emit_active_dispatch",
        "dispatch_id": dispatch_id,
        "fields": changed,
        "original": original,
    }


def _apply_emit_side_effect(state_dir, event: ZfEvent) -> None:
    """Apply deterministic write-through side effects for selected events."""
    if event.type != "task.contract.update":
        return
    try:
        from zf.core.task.store import TaskStore
        from zf.runtime.housekeeping import apply_task_contract_event

        apply_task_contract_event(TaskStore(state_dir / "kanban.json"), event)
    except Exception:
        return


def run_events(args: argparse.Namespace) -> int:
    try:
        context = resolve_project_context(
            explicit_state_dir=getattr(args, "state_dir", None),
        )
    except ConfigError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    state_dir = context.state_dir
    if not state_dir.exists():
        print("Error: not initialized. Run 'zf init' first.", file=sys.stderr)
        return 1

    try:
        event_log = event_log_from_project(state_dir, config=context.config)
    except EventSigningConfigError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    events = event_log.query(type=args.type, last=args.last)

    if getattr(args, "json", False):
        from zf.cli.output import print_result

        print_result(
            command="events",
            data={"events": [asdict(event) for event in events]},
            context=context,
        )
        return 0
    for ev in events:
        payload_str = f" {json.dumps(ev.payload)}" if ev.payload else ""
        print(f"[{ev.ts}] {ev.type}{payload_str}")

    return 0


def run_trace(args: argparse.Namespace) -> int:
    """Render the causation chain of a single event."""
    try:
        context = resolve_project_context(
            explicit_state_dir=getattr(args, "state_dir", None),
        )
    except ConfigError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    state_dir = context.state_dir
    if not state_dir.exists():
        print("Error: not initialized. Run 'zf init' first.", file=sys.stderr)
        return 1

    try:
        event_log = event_log_from_project(state_dir, config=context.config)
    except EventSigningConfigError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    chain = event_log.get_causation_chain(args.event_id)
    if not chain:
        print(f"Error: event {args.event_id} not found", file=sys.stderr)
        return 1

    print(f"Causation chain for {args.event_id} ({len(chain)} events):")
    for i, ev in enumerate(chain):
        arrow = "  " if i == 0 else "→ "
        task = f" task={ev.task_id}" if ev.task_id else ""
        actor = f" actor={ev.actor}" if ev.actor else ""
        print(f"{arrow}[{ev.ts}] {ev.id} {ev.type}{actor}{task}")
    return 0
