"""zf recover — deterministic runtime recovery helpers."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from zf.core.config.loader import ConfigError
from zf.core.config.project_context import resolve_project_context
from zf.core.events.factory import event_log_from_project
from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.core.task.store import TaskStore
from zf.runtime.workflow_resume import (
    apply_workflow_resume,
    build_workflow_resume_projection,
    write_workflow_resume_projection,
)


def register(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "recover",
        help="Recover deterministic runtime state from append-only events",
    )
    nested = parser.add_subparsers(dest="recover_command")

    workflow = nested.add_parser(
        "workflow",
        help="Inspect or resume pending workflow stage handoffs",
    )
    workflow.add_argument("--state-dir", type=Path, default=None)
    workflow.add_argument(
        "--resume-pending",
        action="store_true",
        help="Apply idempotent missing workflow handoffs",
    )
    workflow.add_argument(
        "--dry-run",
        action="store_true",
        help="Build projection without appending recovery events",
    )
    workflow.add_argument(
        "--checkpoint-id",
        default="",
        help=(
            "Apply only one workflow resume checkpoint id "
            "(per-task idempotency key or batch checkpoint id)"
        ),
    )
    workflow.add_argument(
        "--task-map-ref",
        default="",
        help=(
            "Operator-reviewed task_map.json override for a selected batch "
            "resume checkpoint; requires --resume-pending and --checkpoint-id"
        ),
    )
    workflow.add_argument(
        "--force-gate-dispatch",
        action="store_true",
        help=(
            "Operator override: route blocked_external_gate checkpoints "
            "through the out-of-band gate dispatcher (bizsim r4 FIX-2); "
            "events carry mode=operator_forced_gate_dispatch"
        ),
    )
    workflow.add_argument("--json", action="store_true", dest="as_json")
    workflow.set_defaults(func=_run_workflow)

    fanout_terminal = nested.add_parser(
        "fanout-terminal",
        help="Preview or append a narrow fanout child terminal recovery",
    )
    fanout_terminal.add_argument("--state-dir", type=Path, default=None)
    fanout_terminal.add_argument("--fanout-id", required=True)
    fanout_terminal.add_argument("--child-id", required=True)
    fanout_terminal.add_argument("--result-event-id", default="")
    fanout_terminal.add_argument("--status", default="completed")
    fanout_terminal.add_argument("--stage-id", default="")
    fanout_terminal.add_argument("--trace-id", default="")
    fanout_terminal.add_argument("--terminal-event", default="")
    fanout_terminal.add_argument(
        "--aggregate",
        action="store_true",
        help="Also append fanout.aggregate.completed when not already present",
    )
    fanout_terminal.add_argument(
        "--apply",
        action="store_true",
        help="Append the previewed recovery events",
    )
    fanout_terminal.add_argument("--json", action="store_true", dest="as_json")
    fanout_terminal.set_defaults(func=_run_fanout_terminal)

    parser.set_defaults(func=_help)


def _help(args: argparse.Namespace) -> int:
    print(
        "Usage: zf recover workflow|fanout-terminal ...",
        file=sys.stderr,
    )
    return 2


def _run_workflow(args: argparse.Namespace) -> int:
    try:
        context = resolve_project_context(
            explicit_state_dir=args.state_dir,
            load_config_with_explicit=args.state_dir is not None,
        )
    except ConfigError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    state_dir = context.state_dir
    config = context.config
    if not state_dir.exists():
        print(f"Error: state dir {state_dir} does not exist", file=sys.stderr)
        return 1

    try:
        event_log = event_log_from_project(state_dir, config=config, warn=False)
        task_store = TaskStore(state_dir / "kanban.json")
    except Exception as exc:
        print(f"Error: cannot open runtime state: {exc}", file=sys.stderr)
        return 1

    if args.resume_pending:
        if not _run_contract_recovery_allowed(
            config,
            event_log,
            state_dir,
            context.project_root,
            context.config_path,
        ):
            print("Error: run contract drift detected; workflow resume blocked for strict run.", file=sys.stderr)
            return 1
        if str(args.task_map_ref or "").strip() and not str(
            args.checkpoint_id or ""
        ).strip():
            print(
                "Error: --task-map-ref requires --checkpoint-id",
                file=sys.stderr,
            )
            return 1
        # B7 (doc 91 P4): out-of-band gate dispatcher — recover 进程
        # 自带 Orchestrator 实例直接执行缺失孵化(R25 人工
        # _maybe_start_reader_fanout 的制度化),不再依赖瘫痪主循环。
        gate_dispatcher = None
        if not args.dry_run:
            try:
                from zf.runtime.orchestrator import Orchestrator
                from zf.runtime.transport import make_transport

                _orch = Orchestrator(
                    state_dir,
                    config,
                    make_transport(config),
                    project_root=context.project_root,
                )

                def gate_dispatcher(event):
                    _orch._maybe_start_reader_fanout(event)
                    _orch._maybe_start_writer_fanout(event)
            except Exception as exc:
                print(
                    f"Warning: out-of-band dispatcher unavailable "
                    f"({exc}); falling back to marker events",
                    file=sys.stderr,
                )
        result = apply_workflow_resume(
            state_dir,
            config,
            event_writer=EventWriter(event_log),
            task_store=task_store,
            project_root=context.project_root,
            dry_run=bool(args.dry_run),
            gate_dispatcher=gate_dispatcher,
            checkpoint_id=str(args.checkpoint_id or ""),
            override_task_map_ref=str(args.task_map_ref or ""),
            force_gate_dispatch=bool(getattr(args, "force_gate_dispatch", False)),
        )
    else:
        projection = build_workflow_resume_projection(
            state_dir,
            config,
            events=event_log.read_all(),
            tasks=task_store.list_all(),
        )
        projection_path = write_workflow_resume_projection(state_dir, projection)
        result = {
            "schema_version": "workflow-resume.inspect.v0",
            "projection_path": str(projection_path),
            "projection": projection,
            "applied": 0,
            "results": [],
        }

    if args.as_json:
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        summary = result.get("projection", {}).get("summary", {})
        print(f"workflow resume projection: {result.get('projection_path')}")
        print(
            "pending="
            f"{summary.get('pending', 0)} applied={result.get('applied', 0)}"
        )
        if result.get("checkpoint_id"):
            print(f"checkpoint-id: {result.get('checkpoint_id')}")
        if result.get("no_op_reason"):
            print(f"no-op: {result.get('no_op_reason')}")
        for rejection in result.get("rejections", []):
            if isinstance(rejection, dict):
                print(
                    "rejected: "
                    f"{rejection.get('code', 'unknown')} "
                    f"{rejection.get('reason', '')}"
                )
        for item in result.get("results", []):
            checkpoint = item.get("checkpoint") if isinstance(item, dict) else {}
            if not isinstance(checkpoint, dict):
                continue
            print(
                f"- {checkpoint.get('task_id')}: "
                f"{checkpoint.get('safe_resume_action')} -> "
                f"{checkpoint.get('expected_next_role') or checkpoint.get('expected_next_stage')} "
                f"({item.get('reason')})"
            )
        for item in result.get("batch_results", []):
            checkpoint = item.get("checkpoint") if isinstance(item, dict) else {}
            if not isinstance(checkpoint, dict):
                continue
            print(
                f"- batch:{checkpoint.get('checkpoint_id')}: "
                f"{checkpoint.get('safe_resume_action')} -> "
                f"{checkpoint.get('fanout_id') or checkpoint.get('pdd_id')} "
                f"({item.get('reason')})"
            )
    return 1 if result.get("rejected") else 0


def _run_fanout_terminal(args: argparse.Namespace) -> int:
    try:
        context = resolve_project_context(
            explicit_state_dir=args.state_dir,
            load_config_with_explicit=args.state_dir is not None,
        )
    except ConfigError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    state_dir = context.state_dir
    config = context.config
    if not state_dir.exists():
        print(f"Error: state dir {state_dir} does not exist", file=sys.stderr)
        return 1
    try:
        event_log = event_log_from_project(state_dir, config=config, warn=False)
    except Exception as exc:
        print(f"Error: cannot open runtime state: {exc}", file=sys.stderr)
        return 1
    existing = event_log.read_all()
    preview = _fanout_terminal_recovery_preview(args, state_dir, existing)
    if args.apply:
        if not _run_contract_recovery_allowed(
            config,
            event_log,
            state_dir,
            context.project_root,
            context.config_path,
        ):
            print("Error: run contract drift detected; fanout recovery blocked for strict run.", file=sys.stderr)
            return 1
        writer = EventWriter(event_log)
        for event in preview["events"]:
            writer.append(event)
        preview["applied"] = len(preview["events"])
    if args.as_json:
        printable = {
            **preview,
            "events": [json.loads(event.to_json()) for event in preview["events"]],
        }
        print(json.dumps(printable, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        action = "applied" if args.apply else "would-write"
        print(f"fanout-terminal recovery {action}: {len(preview['events'])}")
        for event in preview["events"]:
            print(f"- {event.type}: {event.payload}")
    return 0


def _run_contract_recovery_allowed(
    config,
    event_log,
    state_dir: Path,
    project_root: Path,
    config_path: Path,
) -> bool:
    if config is None:
        return True
    try:
        from zf.runtime.run_contract import evaluate_run_contract_resume_policy

        policy = evaluate_run_contract_resume_policy(
            config,
            config_path=config_path,
            project_root=project_root,
            state_dir=state_dir,
        )
    except Exception:
        return True
    if policy.get("status") in {"STOP", "WARN"}:
        EventWriter(event_log).append(ZfEvent(
            type="config.run_contract.resume_checked",
            actor="zf-cli",
            payload=policy,
        ))
    return policy.get("status") != "STOP"


def _fanout_terminal_recovery_preview(
    args: argparse.Namespace,
    state_dir: Path,
    events: list[ZfEvent],
) -> dict:
    fanout_id = str(args.fanout_id or "").strip()
    child_id = str(args.child_id or "").strip()
    result_event_id = str(args.result_event_id or "").strip()
    manifest = _read_fanout_manifest(state_dir, fanout_id)
    trace_id = str(
        args.trace_id
        or manifest.get("trace_id")
        or ""
    )
    stage_id = str(args.stage_id or manifest.get("stage_id") or "")
    status = str(args.status or "completed").strip() or "completed"
    recovery_events: list[ZfEvent] = []
    if not _fanout_child_terminal_exists(
        events,
        fanout_id=fanout_id,
        child_id=child_id,
        result_event_id=result_event_id,
    ):
        recovery_events.append(ZfEvent(
            type="fanout.child.completed",
            actor="zf-cli",
            correlation_id=trace_id or None,
            payload={
                "schema_version": "fanout-terminal-recovery.v1",
                "source": "zf_recover_fanout_terminal",
                "fanout_id": fanout_id,
                "trace_id": trace_id,
                "stage_id": stage_id,
                "child_id": child_id,
                "status": status,
                "result_event_id": result_event_id,
            },
        ))
    if bool(args.aggregate) and not _fanout_aggregate_terminal_exists(
        events,
        fanout_id=fanout_id,
    ):
        aggregate_payload = {
            "schema_version": "fanout-terminal-recovery.v1",
            "source": "zf_recover_fanout_terminal",
            "fanout_id": fanout_id,
            "trace_id": trace_id,
            "stage_id": stage_id,
            "status": status,
        }
        terminal_event = str(args.terminal_event or "").strip()
        if terminal_event:
            key = "success_event" if status == "completed" else "failure_event"
            aggregate_payload[key] = terminal_event
        recovery_events.append(ZfEvent(
            type="fanout.aggregate.completed",
            actor="zf-cli",
            correlation_id=trace_id or None,
            payload=aggregate_payload,
        ))
    return {
        "schema_version": "fanout-terminal-recovery-preview.v1",
        "state_dir": str(state_dir),
        "fanout_id": fanout_id,
        "child_id": child_id,
        "apply_required": True,
        "applied": 0,
        "events": recovery_events,
    }


def _read_fanout_manifest(state_dir: Path, fanout_id: str) -> dict:
    if not fanout_id or "/" in fanout_id or "\\" in fanout_id:
        return {}
    path = state_dir / "fanouts" / fanout_id / "manifest.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _fanout_child_terminal_exists(
    events: list[ZfEvent],
    *,
    fanout_id: str,
    child_id: str,
    result_event_id: str,
) -> bool:
    for event in events:
        if event.type != "fanout.child.completed":
            continue
        payload = event.payload if isinstance(event.payload, dict) else {}
        if str(payload.get("fanout_id") or "") != fanout_id:
            continue
        if str(payload.get("child_id") or "") != child_id:
            continue
        if (
            result_event_id
            and str(payload.get("result_event_id") or "") != result_event_id
        ):
            continue
        return True
    return False


def _fanout_aggregate_terminal_exists(
    events: list[ZfEvent],
    *,
    fanout_id: str,
) -> bool:
    for event in events:
        if event.type not in {
            "fanout.aggregate.completed",
            "fanout.timed_out",
            "fanout.cancelled",
        }:
            continue
        payload = event.payload if isinstance(event.payload, dict) else {}
        if str(payload.get("fanout_id") or "") == fanout_id:
            return True
    return False


__all__ = ["register"]
