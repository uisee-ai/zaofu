#!/usr/bin/env python3
"""Exercise a real provider response through the durable call-result protocol."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.runtime.artifact_read_ledger import (
    build_attempt_source_manifest,
    build_input_consumption_policy,
    read_attempt_artifact,
)
from zf.runtime.call_result_admission import CallResultAdmissionService
from zf.runtime.call_result_runtime import hydrate_admitted_control_result
from zf.runtime.run_manager import run_goal_completion_gate_event
from zf.runtime.simulation_lifecycle import emit_simulation_done
from zf.runtime.sidecar_refs import write_sidecar_json
from zf.runtime.workflow_operation import WorkflowOperationService


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--backend", required=True, choices=("claude-code", "codex"))
    parser.add_argument("--provider-session-id", required=True)
    parser.add_argument("--state-dir", required=True, type=Path)
    parser.add_argument("--malformed-json", required=True)
    parser.add_argument("--repaired-json", required=True)
    return parser


def _provider_payload(raw: str) -> dict[str, Any]:
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("provider result must be a JSON object")
    return value


def run(args: argparse.Namespace) -> dict[str, Any]:
    state_dir = args.state_dir.resolve()
    state_dir.mkdir(parents=True, exist_ok=False)
    event_log = EventLog(state_dir / "events.jsonl")
    writer = EventWriter(event_log)
    operation_service = WorkflowOperationService(
        state_dir=state_dir,
        event_log=event_log,
        event_writer=writer,
    )
    admission = CallResultAdmissionService(
        state_dir=state_dir,
        event_log=event_log,
        event_writer=writer,
        operation_service=operation_service,
    )
    backend_key = args.backend.replace("-", "_")
    workflow_run_id = f"run-140-{backend_key}"
    task_id = f"TASK-140-{backend_key.upper()}"
    operation_id = f"wop-140-{backend_key}"
    attempt_id = f"attempt-140-{backend_key}"
    writer.append(ZfEvent(
        type="run.goal.started",
        actor="test",
        correlation_id=workflow_run_id,
        payload={"run_id": workflow_run_id, "objective": "durable provider call drill"},
    ))
    ensured = operation_service.ensure_operation(
        workflow_run_id=workflow_run_id,
        operation_id=operation_id,
        operation_type="agent",
        request={
            "backend": args.backend,
            "task_id": task_id,
            "prompt": "durable call-result drill",
        },
        parent_stage_id="verify",
        task_id=task_id,
        correlation_id=workflow_run_id,
    )
    operation_service.mark_started(
        operation_id=operation_id,
        request_hash=ensured.request_hash,
        workflow_run_id=workflow_run_id,
        task_id=task_id,
        provider_session_id=args.provider_session_id,
        correlation_id=workflow_run_id,
    )

    source_descriptor = write_sidecar_json(
        state_dir,
        f"artifacts/provider-drill/{backend_key}/contract.json",
        {"acceptance": "provider must return an immutable target commit"},
        kind="provider_drill_input",
        schema_version="provider-drill-input.v1",
        created_by="durable-call-result-provider-drill",
        required=True,
    )
    source_manifest = build_attempt_source_manifest(
        workflow_run_id=workflow_run_id,
        task_id=task_id,
        attempt_id=attempt_id,
        dispatch_id=attempt_id,
        sources=[{
            "source_id": "contract",
            "artifact_id": "contract.json",
            "kind": "contract",
            "ref": source_descriptor["ref"],
            "sha256": source_descriptor["sha256"],
            "allowed_paths": ["$"],
        }],
    )
    policy = build_input_consumption_policy(
        workflow_run_id=workflow_run_id,
        attempt_id=attempt_id,
        required_reads=[{
            "source_id": "contract",
            "artifact_id": "contract.json",
            "artifact_sha256": source_descriptor["sha256"],
            "json_path": "$",
            "min_returned_bytes": 1,
        }],
    )
    operation = {
        "workflow_run_id": workflow_run_id,
        "operation_id": operation_id,
        "request_hash": ensured.request_hash,
    }
    common = {
        "workflow_run_id": workflow_run_id,
        "operation_id": operation_id,
        "request_hash": ensured.request_hash,
        "attempt_id": attempt_id,
        "provider_session_id": args.provider_session_id,
        "input_consumption_policy": policy,
    }
    malformed = _provider_payload(args.malformed_json)
    malformed_event = ZfEvent(
        type="dev.build.done",
        actor=args.backend,
        task_id=task_id,
        correlation_id=workflow_run_id,
        payload={**common, **malformed},
    )
    first = admission.report_legacy_result(
        malformed_event,
        mode="blocking",
        operation=operation,
        input_policy=policy,
    )
    if not first.repair_requested:
        raise AssertionError(f"expected repair_pending, got {first.status}: {first.issues}")

    read_attempt_artifact(
        state_dir,
        manifest=source_manifest,
        source_id="contract",
        artifact_id="contract.json",
    )
    repaired = _provider_payload(args.repaired_json)
    repaired_event = ZfEvent(
        type="dev.build.done",
        actor=args.backend,
        task_id=task_id,
        correlation_id=workflow_run_id,
        payload={**common, **repaired},
    )
    second = admission.report_legacy_result(
        repaired_event,
        mode="blocking",
        operation=operation,
        input_policy=policy,
    )
    if not second.admitted or not second.envelope_ref:
        raise AssertionError(f"expected admitted result, got {second.status}: {second.issues}")

    restarted_service = WorkflowOperationService(
        state_dir=state_dir,
        event_log=EventLog(state_dir / "events.jsonl"),
        event_writer=EventWriter(EventLog(state_dir / "events.jsonl")),
    )
    replay = restarted_service.ensure_operation(
        workflow_run_id=workflow_run_id,
        operation_id=operation_id,
        operation_type="agent",
        request={
            "backend": args.backend,
            "task_id": task_id,
            "prompt": "durable call-result drill",
        },
        parent_stage_id="verify",
        task_id=task_id,
        correlation_id=workflow_run_id,
    )
    control_result = hydrate_admitted_control_result(state_dir, second.envelope_ref)
    if replay.status != "settled" or not replay.replay_hit:
        raise AssertionError(f"restart did not hit settled operation: {replay}")
    if replay.admitted_call_result_digest != second.envelope_ref["sha256"]:
        raise AssertionError("restart returned a different admitted envelope")

    claim = ZfEvent(
        type="run.goal.completion.claimed",
        actor="test",
        correlation_id=workflow_run_id,
        payload={"run_id": workflow_run_id, "objective": "durable provider call drill"},
    )
    goal = run_goal_completion_gate_event(
        [*event_log.read_all(), claim],
        claim=claim,
        required_operation_ids=[operation_id],
    )
    if goal is None or goal.type != "run.goal.completed":
        raise AssertionError(f"goal closure failed: {goal}")
    goal = writer.append(goal)
    if emit_simulation_done(
        goal,
        events=event_log.read_all(),
        writer=writer,
    ) is None:
        raise AssertionError("simulation.done was not emitted from the run terminal")
    counts: dict[str, int] = {}
    for event in event_log.read_all():
        counts[event.type] = counts.get(event.type, 0) + 1
    return {
        "schema_version": "durable-call-result-provider-drill.v1",
        "backend": args.backend,
        "provider_session_id": args.provider_session_id,
        "workflow_run_id": workflow_run_id,
        "operation_id": operation_id,
        "first_status": first.status,
        "repair_round": first.repair_round,
        "second_status": second.status,
        "replay_status": replay.status,
        "replay_hit": replay.replay_hit,
        "goal_event": goal.type,
        "target_commit": control_result.get("target_commit"),
        "envelope_ref": second.envelope_ref,
        "event_counts": counts,
    }


def main() -> int:
    result = run(_parser().parse_args())
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
