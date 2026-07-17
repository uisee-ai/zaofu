"""Regression coverage for the diagnosis-gated self-repair boundary."""

from __future__ import annotations

import json
from pathlib import Path

from zf.autoresearch.self_repair import (
    candidate_from_trigger_event,
    write_candidate_artifact,
)
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.runtime.autoresearch_invocation import (
    build_invocation_request_from_run_manager_event,
)
from zf.runtime.run_manager import (
    RUN_MANAGER_AUTORESEARCH_REQUESTED,
    _consume_autoresearch_results,
    _pending_attention_diagnostic_actions,
)


def _state_dir(tmp_path: Path) -> Path:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    return state_dir


def _candidate_created(
    state_dir: Path,
    *,
    request_id: str,
    recovery_case_id: str,
    failure_scope: str = "runtime",
    expected_fault: bool = False,
) -> ZfEvent:
    trigger = ZfEvent(
        type="autoresearch.trigger.accepted",
        id="evt-trigger",
        actor="zf-autoresearch",
        task_id="TASK-1",
        correlation_id="run-1",
        payload={
            "trigger_id": "ar-trigger-1",
            "source": "autoresearch.invocation.accepted",
            "fingerprint": "task_ref_rejected:TASK-1:evt-source",
            "recovery_case_id": recovery_case_id,
            "request_id": request_id,
            "failure_scope": failure_scope,
            "expected_fault": expected_fault,
            "evidence_paths": ["records/trigger.md"],
        },
    )
    candidate = candidate_from_trigger_event(trigger)
    path = write_candidate_artifact(state_dir, candidate)
    return ZfEvent(
        type="autoresearch.bug_candidate.created",
        actor="zf-autoresearch",
        task_id="TASK-1",
        correlation_id="run-1",
        payload={
            "candidate": candidate.to_dict(),
            "candidate_path": str(path),
            "repair_task_payload": None,
        },
    )


def _request(request_id: str, recovery_case_id: str) -> ZfEvent:
    return ZfEvent(
        type=RUN_MANAGER_AUTORESEARCH_REQUESTED,
        actor="run-manager",
        task_id="TASK-1",
        correlation_id="run-1",
        payload={
            "request_id": request_id,
            "recovery_case_id": recovery_case_id,
            "fingerprint": "task_ref_rejected:TASK-1:evt-source",
            "workflow_run_id": "run-1",
            "source_event_ids": ["evt-source"],
        },
    )


def test_candidate_confirms_only_after_structured_reproduction(tmp_path: Path) -> None:
    state_dir = _state_dir(tmp_path)
    log = EventLog(state_dir / "events.jsonl")
    writer = EventWriter(log)
    request = _request("rmar-1", "rcase-1")
    created = _candidate_created(
        state_dir,
        request_id="rmar-1",
        recovery_case_id="rcase-1",
    )
    result = ZfEvent(
        type="autoresearch.loop.completed",
        actor="zf-autoresearch-resident",
        task_id="TASK-1",
        correlation_id="run-1",
        payload={
            "loop_request_id": "rmar-1",
            "diagnosis": {
                "reproduction_status": "reproduced",
                "evidence_refs": ["reports/reproduced-task-ref.md"],
                "repair_scope": [
                    "src/zf/runtime/task_refs.py",
                    "tests/test_task_refs.py",
                ],
            },
        },
    )
    log.append(request)
    log.append(created)
    log.append(result)

    assert _consume_autoresearch_results(state_dir, log.read_all(), writer) == 2
    events = log.read_all()
    confirmed = next(
        event for event in events
        if event.type == "autoresearch.bug_candidate.confirmed"
    )
    assert confirmed.payload["candidate"]["status"] == "confirmed"
    assert confirmed.payload["repair_task_payload"]["contract"]["scope"] == [
        "src/zf/runtime/task_refs.py",
        "tests/test_task_refs.py",
    ]
    assert _consume_autoresearch_results(state_dir, events, writer) == 0


def test_expected_plan_admission_candidate_is_dismissed_even_if_reproduced(
    tmp_path: Path,
) -> None:
    state_dir = _state_dir(tmp_path)
    log = EventLog(state_dir / "events.jsonl")
    writer = EventWriter(log)
    request = _request("rmar-plan", "rcase-plan")
    created = _candidate_created(
        state_dir,
        request_id="rmar-plan",
        recovery_case_id="rcase-plan",
        failure_scope="plan_admission",
        expected_fault=True,
    )
    result = ZfEvent(
        type="autoresearch.loop.completed",
        actor="zf-autoresearch-resident",
        task_id="TASK-1",
        correlation_id="run-1",
        payload={
            "loop_request_id": "rmar-plan",
            "diagnosis": {
                "reproduction_status": "confirmed",
                "evidence_paths": ["reports/expected-plan-fault.md"],
                "repair_scope": ["src/zf/runtime/writer_fanout_admission.py"],
            },
        },
    )
    log.append(request)
    log.append(created)
    log.append(result)

    assert _consume_autoresearch_results(state_dir, log.read_all(), writer) == 2
    dismissed = next(
        event for event in log.read_all()
        if event.type == "autoresearch.bug_candidate.dismissed"
    )
    assert dismissed.payload["candidate"]["status"] == "dismissed"
    assert dismissed.payload["repair_task_payload"] is None


def test_run_manager_does_not_turn_plan_revision_attention_into_diagnosis(
    tmp_path: Path,
) -> None:
    state_dir = _state_dir(tmp_path)
    projection_path = state_dir / "projections" / "supervisor" / "snapshot.json"
    projection_path.parent.mkdir(parents=True)
    projection_path.write_text(json.dumps({
        "attention_items": [{
            "attention_id": "attn-plan",
            "fingerprint": "plan_admission:plan-1",
            "status": "open",
            "suggested_route": "plan_revision",
            "failure_scope": "plan_admission",
            "source_event_ids": ["evt-plan"],
        }],
    }), encoding="utf-8")

    assert _pending_attention_diagnostic_actions(state_dir, []) == []


def test_same_recovery_case_bridges_only_one_autoresearch_invocation() -> None:
    first_request = _request("rmar-first", "rcase-shared")
    first_invocation = build_invocation_request_from_run_manager_event(
        first_request,
        events=[first_request],
    )

    assert first_invocation is not None
    second_request = _request("rmar-second", "rcase-shared")
    assert build_invocation_request_from_run_manager_event(
        second_request,
        events=[first_request, first_invocation, second_request],
    ) is None
