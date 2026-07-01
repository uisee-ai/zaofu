"""Tests for loop.v1 projection (doc94)."""

from __future__ import annotations

from zf.core.events.model import ZfEvent
from zf.core.events.log import EventLog
from zf.core.events.writer import EventWriter
from zf.runtime.loop_closure import append_loop_closure_events
from zf.runtime.loop_projection import (
    build_loop_projection,
    related_loop_ids_for_delivery_trace,
)

_NOW = "2026-06-15T12:00:00+00:00"


def _event(event_type: str, event_id: str, **kwargs) -> ZfEvent:
    return ZfEvent(type=event_type, id=event_id, ts=_NOW, **kwargs)


def test_loop_projection_groups_owner_signals() -> None:
    events = list(enumerate([
        _event(
            "static_gate.failed",
            "gate-fail",
            task_id="T1",
            payload={"feature_id": "F-1", "reason": "pytest failed"},
        ),
        _event(
            "task.rework.triage.completed",
            "triage-gap",
            task_id="T1",
            payload={
                "feature_id": "F-1",
                "classification": "evidence_payload_gap",
                "reason": "missing test evidence",
            },
        ),
        _event(
            "task.rework.requested",
            "rework-1",
            task_id="T1",
            payload={"feature_id": "F-1", "reason": "gate failed"},
        ),
        _event(
            "worker.stuck",
            "stuck-1",
            task_id="T1",
            actor="dev-1",
            payload={"feature_id": "F-1", "role_instance": "dev-1"},
        ),
        _event(
            "fanout.aggregate.started",
            "fanout-retry",
            payload={
                "feature_id": "F-1",
                "fanout_id": "fanout-1",
                "retry_count": 1,
                "task_ids": ["T1", "T2"],
            },
        ),
        _event(
            "autoresearch.loop.completed",
            "ar-done",
            task_id="T1",
            correlation_id="ar-run-1",
            payload={"feature_id": "F-1", "score_delta": 12.4},
        ),
        _event(
            "replan.contract_eval.completed",
            "replan-eval",
            payload={
                "feature_id": "F-1",
                "decision": "revise",
                "new_task_map_ref": "artifacts/F-1/task-map-v2.json",
            },
        ),
    ]))

    projection = build_loop_projection(events=events, generated_at=_NOW, project_id="proj")

    assert projection["schema_version"] == "loop.v1"
    assert projection["summary"]["total"] == 7
    assert projection["summary"]["by_kind"]["gate_failure"] == 1
    assert projection["summary"]["by_kind"]["missing_evidence"] == 1
    assert projection["summary"]["by_kind"]["stuck_worker"] == 1
    assert projection["summary"]["by_kind"]["fanout_retry"] == 1
    assert projection["summary"]["by_kind"]["autoresearch"] == 1
    assert projection["summary"]["by_kind"]["replan"] == 1
    assert {item["kind"] for item in projection["behaviors"]} == {"missing_evidence", "stuck_worker"}
    assert any(item["kind"] == "functional_check" for item in projection["evals"])
    assert projection["summary"]["candidate_count"] >= 5
    assert projection["summary"]["verification_count"] == 0
    assert projection["summary"]["learning_count"] == 0
    assert any(item["fix_layer"] == "gate_evidence" for item in projection["diagnoses"])
    assert any(item["fix_layer"] == "agent_runtime" for item in projection["diagnoses"])
    assert any(item["fix_layer"] == "replan" for item in projection["diagnoses"])
    assert all(loop["loop_id"].startswith(f"loop:{loop['kind']}:") for loop in projection["loops"])


def test_loop_projection_marks_gate_recovered_by_later_pass() -> None:
    events = list(enumerate([
        _event("static_gate.failed", "gate-fail", task_id="T1", payload={"feature_id": "F-1"}),
        _event("static_gate.passed", "gate-pass", task_id="T1", payload={"feature_id": "F-1"}),
    ]))

    projection = build_loop_projection(events=events, generated_at=_NOW)

    loop = projection["loops"][0]
    assert loop["kind"] == "gate_failure"
    assert loop["status"] == "recovered"
    assert loop["event_ids"] == ["gate-fail", "gate-pass"]


def test_related_loop_ids_for_delivery_trace_matches_feature_and_task() -> None:
    events = list(enumerate([
        _event("static_gate.failed", "gate-fail", task_id="T1", payload={"feature_id": "F-1"}),
        _event("worker.stuck", "stuck-other", task_id="T9", payload={"feature_id": "F-9"}),
    ]))
    projection = build_loop_projection(events=events, generated_at=_NOW)
    trace = {
        "feature_id": "F-1",
        "trace_id": "trace-F-1",
        "execution_graph": {"nodes": [{"task_id": "T1"}]},
    }

    related = related_loop_ids_for_delivery_trace(trace=trace, loop_projection=projection)

    assert len(related) == 1
    assert related[0].startswith("loop:gate_failure:")


def test_loop_projection_includes_action_timeline() -> None:
    events = list(enumerate([
        _event("loop.action.requested", "loop-action", task_id="T1", payload={
            "action_id": "la-1",
            "loop_id": "loop:stuck_worker:abc",
            "candidate_id": "candidate:stuck_worker:abc",
            "suggested_action": "inspect_worker_liveness",
            "source_kind": "stuck_worker",
            "evidence_refs": ["stuck-1"],
        }),
        _event("repair.action.requested", "repair-request", task_id="T1", causation_id="loop-action", payload={
            "action_id": "ra-1",
            "kind": "restart_worker",
            "source_loop_action_id": "la-1",
        }),
        _event("loop.action.mapped", "loop-mapped", task_id="T1", causation_id="loop-action", payload={
            "action_id": "la-1",
            "loop_id": "loop:stuck_worker:abc",
            "candidate_id": "candidate:stuck_worker:abc",
            "suggested_action": "inspect_worker_liveness",
            "mapped_event_id": "repair-request",
            "mapped_event_type": "repair.action.requested",
            "mapped_action": "restart_worker",
            "downstream_action_id": "ra-1",
        }),
        _event("repair.action.applied", "repair-applied", task_id="T1", causation_id="repair-request", payload={
            "action_id": "ra-1",
            "kind": "restart_worker",
            "reason": "worker restarted",
        }),
    ]))

    projection = build_loop_projection(events=events, generated_at=_NOW, project_id="proj")

    action = projection["actions"][0]
    assert action["action_id"] == "la-1"
    assert action["status"] == "applied"
    assert action["mapped_event_id"] == "repair-request"
    assert action["terminal_event_id"] == "repair-applied"
    assert action["outcome"] == "worker restarted"


def test_loop_projection_derives_diagnosis_verify_and_learning() -> None:
    base_events = [
        _event(
            "worker.stuck",
            "stuck-worker",
            actor="dev-1",
            task_id="T1",
            payload={"feature_id": "F-1", "role_instance": "dev-1"},
        ),
    ]
    base_projection = build_loop_projection(events=list(enumerate(base_events)), generated_at=_NOW, project_id="proj")
    candidate = base_projection["candidates"][0]
    loop_id = candidate["loop_id"]
    candidate_id = candidate["candidate_id"]
    events = list(enumerate([
        *base_events,
        _event("loop.action.requested", "loop-action", task_id="T1", payload={
            "action_id": "la-1",
            "loop_id": loop_id,
            "candidate_id": candidate_id,
            "suggested_action": "inspect_worker_liveness",
            "source_kind": "stuck_worker",
            "evidence_refs": ["stuck-worker"],
        }),
        _event("repair.action.requested", "repair-request", task_id="T1", causation_id="loop-action", payload={
            "action_id": "ra-1",
            "kind": "restart_worker",
            "source_loop_action_id": "la-1",
        }),
        _event("loop.action.mapped", "loop-mapped", task_id="T1", causation_id="loop-action", payload={
            "action_id": "la-1",
            "loop_id": loop_id,
            "candidate_id": candidate_id,
            "suggested_action": "inspect_worker_liveness",
            "mapped_event_id": "repair-request",
            "mapped_event_type": "repair.action.requested",
            "mapped_action": "restart_worker",
            "downstream_action_id": "ra-1",
        }),
        _event("repair.action.applied", "repair-applied", task_id="T1", causation_id="repair-request", payload={
            "action_id": "ra-1",
            "kind": "restart_worker",
            "source_loop_action_id": "la-1",
            "reason": "worker restarted",
        }),
        _event("worker.stuck.recovered", "worker-recovered", task_id="T1", actor="dev-1", payload={
            "feature_id": "F-1",
        }),
    ]))

    projection = build_loop_projection(events=events, generated_at=_NOW, project_id="proj")

    candidate = projection["candidates"][0]
    assert candidate["diagnosis"]["fix_layer"] == "agent_runtime"
    assert candidate["diagnosis"]["recommended_action"] == "inspect_worker_liveness"
    assert projection["actions"][0]["latest_verification_status"] == "passed"
    assert projection["verifications"][0]["status"] == "passed"
    assert projection["verifications"][0]["result"] == "passed"
    assert projection["verifications"][0]["source_action_id"] == "la-1"
    assert projection["verifications"][0]["missing_evidence"] == []
    assert projection["verifications"][0]["next_check"] == ""
    assert projection["verifications"][0]["evidence_refs"][-1] == "worker-recovered"
    assert projection["learning"][0]["artifact_kind"] == "runbook_note"
    assert projection["learning"][0]["promotion_path"] == "operator_review -> runbook"
    assert projection["summary"]["verification_count"] == 1
    assert projection["summary"]["learning_count"] == 1


def test_loop_projection_attaches_learning_promotion_status() -> None:
    base_events = [
        _event(
            "worker.stuck",
            "stuck-worker",
            actor="dev-1",
            task_id="T1",
            payload={"feature_id": "F-1", "role_instance": "dev-1"},
        ),
    ]
    base_projection = build_loop_projection(events=list(enumerate(base_events)), generated_at=_NOW, project_id="proj")
    candidate = base_projection["candidates"][0]
    loop_id = candidate["loop_id"]
    candidate_id = candidate["candidate_id"]
    action_events = [
        _event("loop.action.requested", "loop-action", task_id="T1", payload={
            "action_id": "la-1",
            "loop_id": loop_id,
            "candidate_id": candidate_id,
            "suggested_action": "inspect_worker_liveness",
            "source_kind": "stuck_worker",
            "evidence_refs": ["stuck-worker"],
        }),
        _event("repair.action.requested", "repair-request", task_id="T1", causation_id="loop-action", payload={
            "action_id": "ra-1",
            "kind": "restart_worker",
            "source_loop_action_id": "la-1",
        }),
        _event("loop.action.mapped", "loop-mapped", task_id="T1", causation_id="loop-action", payload={
            "action_id": "la-1",
            "loop_id": loop_id,
            "candidate_id": candidate_id,
            "suggested_action": "inspect_worker_liveness",
            "mapped_event_id": "repair-request",
            "mapped_event_type": "repair.action.requested",
            "mapped_action": "restart_worker",
            "downstream_action_id": "ra-1",
        }),
        _event("repair.action.applied", "repair-applied", task_id="T1", causation_id="repair-request", payload={
            "action_id": "ra-1",
            "kind": "restart_worker",
            "source_loop_action_id": "la-1",
            "reason": "worker restarted",
        }),
        _event("worker.stuck.recovered", "worker-recovered", task_id="T1", actor="dev-1", payload={
            "feature_id": "F-1",
        }),
    ]
    learning_projection = build_loop_projection(
        events=list(enumerate([*base_events, *action_events])),
        generated_at=_NOW,
        project_id="proj",
    )
    learning = learning_projection["learning"][0]
    learning_id = learning["learning_id"]
    promoted_events = [
        _event("loop.learning.promotion.requested", "promotion-requested", task_id="T1", payload={
            "promotion_id": "lp-1",
            "learning_id": learning_id,
            "loop_id": loop_id,
            "candidate_id": candidate_id,
            "target": "runbook_note",
        }),
        _event("loop.learning.promotion.materialized", "promotion-materialized", task_id="T1", payload={
            "promotion_id": "lp-1",
            "learning_id": learning_id,
            "loop_id": loop_id,
            "candidate_id": candidate_id,
            "target": "runbook_note",
            "proposal_ref": "loop/promotions/lp-1.json",
        }),
    ]

    projection = build_loop_projection(
        events=list(enumerate([*base_events, *action_events, *promoted_events])),
        generated_at=_NOW,
        project_id="proj",
    )

    row = projection["learning"][0]
    assert row["learning_id"] == learning_id
    assert row["promotion_status"] == "materialized"
    assert row["promotion_target"] == "runbook_note"
    assert row["promotion_ref"] == "loop/promotions/lp-1.json"
    assert row["promotion_event_ids"] == ["promotion-requested", "promotion-materialized"]


def test_loop_projection_explains_autoresearch_missing_artifact_refs() -> None:
    base_events = [
        _event("autoresearch.loop.failed", "ar-failed", task_id="T1", correlation_id="ar-run-1", payload={
            "feature_id": "F-1",
            "reason": "insufficient evidence",
        }),
    ]
    base_projection = build_loop_projection(events=list(enumerate(base_events)), generated_at=_NOW, project_id="proj")
    candidate = base_projection["candidates"][0]
    loop_id = candidate["loop_id"]
    candidate_id = candidate["candidate_id"]
    events = list(enumerate([
        *base_events,
        _event("loop.action.requested", "loop-action", task_id="T1", payload={
            "action_id": "la-ar",
            "loop_id": loop_id,
            "candidate_id": candidate_id,
            "suggested_action": "review_autoresearch_result",
            "source_kind": "autoresearch",
            "evidence_refs": ["ar-failed"],
        }),
        _event("autoresearch.loop.completed", "ar-completed", task_id="T1", correlation_id="ar-run-1", payload={
            "feature_id": "F-1",
            "source_loop_action_id": "la-ar",
            "status": "completed",
        }),
    ]))

    projection = build_loop_projection(events=events, generated_at=_NOW, project_id="proj")

    verification = projection["verifications"][0]
    assert verification["status"] == "inconclusive"
    assert verification["result"] == "inconclusive"
    assert verification["source_action_id"] == "la-ar"
    assert verification["missing_evidence"] == ["artifact_ref", "proposal_ref", "candidate_path", "report_ref"]
    assert verification["next_check"] == "wait_for_autoresearch_artifact"
    assert verification["reason"] == "autoresearch completed without proposal artifact refs"


def test_loop_closure_appends_verify_and_learning_events(tmp_path) -> None:
    base_events = [
        _event("worker.stuck", "stuck-worker", actor="dev-1", task_id="T1", payload={"feature_id": "F-1", "role_instance": "dev-1"}),
    ]
    base_projection = build_loop_projection(events=list(enumerate(base_events)), generated_at=_NOW, project_id="proj")
    candidate = base_projection["candidates"][0]
    loop_id = candidate["loop_id"]
    candidate_id = candidate["candidate_id"]
    terminal_events = [
        _event("loop.action.requested", "loop-action", task_id="T1", payload={
            "action_id": "la-1",
            "loop_id": loop_id,
            "candidate_id": candidate_id,
            "suggested_action": "inspect_worker_liveness",
            "source_kind": "stuck_worker",
            "evidence_refs": ["stuck-worker"],
        }),
        _event("repair.action.requested", "repair-request", task_id="T1", causation_id="loop-action", payload={
            "action_id": "ra-1",
            "kind": "restart_worker",
            "source_loop_action_id": "la-1",
        }),
        _event("loop.action.mapped", "loop-mapped", task_id="T1", causation_id="loop-action", payload={
            "action_id": "la-1",
            "loop_id": loop_id,
            "candidate_id": candidate_id,
            "suggested_action": "inspect_worker_liveness",
            "mapped_event_id": "repair-request",
            "mapped_event_type": "repair.action.requested",
            "mapped_action": "restart_worker",
            "downstream_action_id": "ra-1",
        }),
        _event("repair.action.applied", "repair-applied", task_id="T1", causation_id="repair-request", payload={
            "action_id": "ra-1",
            "kind": "restart_worker",
            "source_loop_action_id": "la-1",
            "reason": "worker restarted",
        }),
        _event("worker.stuck.recovered", "worker-recovered", task_id="T1", actor="dev-1", payload={"feature_id": "F-1"}),
    ]
    events = [*base_events, *terminal_events]
    log = EventLog(tmp_path / "events.jsonl")
    writer = EventWriter(log)

    emitted = append_loop_closure_events(
        events=events,
        source_event=terminal_events[-1],
        writer=writer,
        state_dir=tmp_path,
        project_id="proj",
    )

    emitted_types = [event.type for event in emitted]
    assert "loop.verify.requested" in emitted_types
    assert "loop.verify.completed" in emitted_types
    assert "loop.learning.materialized" in emitted_types
    completed = next(event for event in emitted if event.type == "loop.verify.completed")
    assert completed.payload["source_action_id"] == "la-1"
    assert completed.payload["result"] == "passed"
    assert completed.payload["missing_evidence"] == []
    assert completed.payload["next_check"] == ""
    written = list((tmp_path / "loop" / "learning").glob("*.json"))
    assert written
