from __future__ import annotations

from zf.core.events.model import ZfEvent
from zf.runtime.recovery_case import (
    build_recovery_case_projection,
    build_recovery_metrics,
    converge_recovery_actions,
    recovery_action_admission,
    recovery_case_id_from_payload,
)


def _event(event_type: str, case_id: str, **payload: object) -> ZfEvent:
    return ZfEvent(
        type=event_type,
        actor="run-manager",
        task_id=str(payload.get("task_id") or "TASK-1"),
        correlation_id="workflow-1",
        payload={
            "recovery_case_id": case_id,
            "workflow_run_id": "workflow-1",
            "task_id": "TASK-1",
            "task_map_generation": "generation-1",
            **payload,
        },
    )


def _action(**overrides: object) -> dict[str, object]:
    return {
        "action": "candidate-rework-apply",
        "safe_resume_action": "trigger_rework",
        "workflow_run_id": "workflow-1",
        "task_id": "TASK-1",
        "task_map_generation": "generation-1",
        "target_commit": "a" * 40,
        "failure_scope": "candidate",
        "failure_class": "product_acceptance_gap",
        "fingerprint": "missing-user-journey",
        "operation_key": "operation-1",
        **overrides,
    }


def test_case_identity_excludes_transport_and_incident_ids() -> None:
    first = _action(
        source_event_id="evt-1",
        source_event_ids=["evt-1"],
        plan_admission_incident_id="incident-1",
        request_id="request-1",
    )
    second = _action(
        source_event_id="evt-2",
        source_event_ids=["evt-2"],
        plan_admission_incident_id="incident-428",
        request_id="request-2",
    )

    assert recovery_case_id_from_payload(first) == recovery_case_id_from_payload(second)


def test_case_identity_changes_with_generation_or_target() -> None:
    baseline = recovery_case_id_from_payload(_action())

    assert baseline != recovery_case_id_from_payload(
        _action(task_map_generation="generation-2")
    )
    assert baseline != recovery_case_id_from_payload(
        _action(target_commit="b" * 40)
    )


def test_pending_actions_converge_to_one_action_per_case() -> None:
    actions, projection = converge_recovery_actions(
        [],
        [
            _action(operation_key="operation-1"),
            _action(operation_key="operation-2", source_event_id="evt-later"),
        ],
    )

    assert len(actions) == 1
    assert actions[0]["recovery_admission"]["status"] == "admitted"
    assert projection["summary"]["pending_suppressed"] == 1
    assert projection["suppressed_actions"][0]["reason"] == "duplicate_pending_action"


def test_active_effect_blocks_a_second_expensive_action() -> None:
    case_id = recovery_case_id_from_payload(_action())
    events = [
        _event("run.manager.action.planned", case_id),
        _event("run.manager.action.applied", case_id),
        _event("run.manager.action.effect.pending", case_id),
    ]

    actions, projection = converge_recovery_actions(events, [_action()])
    admission = recovery_action_admission(events, _action())

    assert actions == []
    assert projection["cases"][0]["status"] == "verifying"
    assert projection["suppressed_actions"][0]["reason"] == "active_effect"
    assert admission["status"] == "blocked"
    assert admission["reason"] == "active_effect"


def test_failed_effect_reopens_then_global_no_progress_limit_blocks() -> None:
    case_id = recovery_case_id_from_payload(_action())
    events: list[ZfEvent] = []
    for ordinal in range(3):
        events.extend([
            _event("run.manager.action.planned", case_id, attempt=ordinal + 1),
            _event("run.manager.action.applied", case_id, attempt=ordinal + 1),
            _event("run.manager.action.effect.pending", case_id, attempt=ordinal + 1),
            _event("run.manager.action.effect.failed", case_id, attempt=ordinal + 1),
        ])

    projection = build_recovery_case_projection(events)
    admission = recovery_action_admission(events, _action())

    assert projection["cases"][0]["status"] == "open"
    assert projection["cases"][0]["consecutive_failures"] == 3
    assert admission["status"] == "blocked"
    assert admission["reason"] == "case_no_progress_limit"


def test_verified_effect_resolves_case_and_resets_failure_count() -> None:
    case_id = recovery_case_id_from_payload(_action())
    events = [
        _event("run.manager.action.effect.failed", case_id),
        _event("run.manager.action.effect.pending", case_id),
        _event("run.manager.action.effect.passed", case_id),
    ]

    projection = build_recovery_case_projection(events)

    assert projection["cases"][0]["status"] == "resolved"
    assert projection["cases"][0]["consecutive_failures"] == 0


def test_explicit_legacy_case_id_remains_authoritative() -> None:
    assert recovery_case_id_from_payload({"recovery_case_id": "rcase-legacy"}) == (
        "rcase-legacy"
    )


def test_recovery_metrics_count_effects_not_triage_or_replay() -> None:
    events = [
        _event("orchestrator.rework.triage.requested", "case-1"),
        _event("run.manager.action.planned", "case-1"),
        _event(
            "task.rework.requested",
            "case-1",
            attempt=1,
            failure_fingerprint="acceptance-gap",
        ),
        _event(
            "task.rework.requested",
            "case-1",
            attempt=1,
            failure_fingerprint="acceptance-gap",
        ),
        _event(
            "workflow.call.result.repair.requested",
            "case-protocol",
            operation_id="operation-1",
            request_hash="hash-1",
            repair_round=1,
            semantic_attempt_incremented=False,
        ),
        _event(
            "workflow.operation.retry_started",
            "case-env",
            operation_id="operation-2",
            retry_attempt=1,
            recovery_class="transient_transport",
        ),
        _event(
            "run.goal.completion.blocked",
            "case-provider",
            claim_id="claim-1",
            blocker_fingerprint="provider-wait",
            blockers=["waiting_external_provider"],
            external_wait={
                "kind": "provider_qualification",
                "status": "failed",
                "semantic_attempt_incremented": False,
            },
        ),
        _event(
            "run.goal.completion.blocked",
            "case-provider",
            claim_id="claim-1",
            blocker_fingerprint="provider-wait",
            blockers=["waiting_external_provider"],
            external_wait={"kind": "provider_qualification", "status": "failed"},
        ),
        _event(
            "workflow.operation.superseded",
            "case-stale",
            operation_id="operation-old",
            request_hash="hash-old",
        ),
    ]

    projection = build_recovery_metrics(events)

    assert projection["counts"] == {
        "semantic_rework": 1,
        "protocol_repair": 1,
        "environment_retry": 1,
        "external_wait": 1,
        "superseded": 1,
    }
    assert projection["total_effects"] == 5


def test_replan_delivery_receipt_does_not_resolve_original_failure():
    events = [
        _event("run.manager.action.effect.failed", "case"),
        _event("run.manager.action.verify.passed", "case", action="semantic-replan-request"),
        _event("task_map.ready", "case"),
    ]
    case = build_recovery_case_projection(events)["cases"][0]
    assert case["status"] == "verifying"
    assert case["consecutive_failures"] == 1


def test_effect_settlement_requires_matching_task(tmp_path):
    from zf.core.events.log import EventLog
    from zf.core.events.writer import EventWriter
    from zf.runtime.run_manager import _settle_pending_action_effects
    log = EventLog(tmp_path / "events.jsonl")
    writer = EventWriter(log)
    pending = _event("run.manager.action.effect.pending", "case",
                     effect_id="effect", action="semantic-replan-request",
                     expected_event_types=["task.done"], failure_event_types=["dev.blocked"])
    log.append(pending)
    log.append(_event("task.done", "other-case", task_id="OTHER"))
    assert _settle_pending_action_effects(log.read_all(), writer) == 0
    log.append(_event("task.done", "case"))
    assert _settle_pending_action_effects(log.read_all(), writer) == 1


def test_cross_task_cancel_reopens_semantic_replan_effect_once(tmp_path):
    from zf.core.events.log import EventLog
    from zf.core.events.writer import EventWriter
    from zf.runtime.run_manager import _settle_pending_action_effects

    log = EventLog(tmp_path / "events.jsonl")
    writer = EventWriter(log)
    pending = _event("run.manager.action.effect.pending", "case", effect_id="effect",
                     action="semantic-replan-request", source_event_id="failed-a",
                     request_id="request-key-a")
    log.append(pending)
    for event in [
        ZfEvent(id="request-a", type="flow.discovery.requested", payload={
            "schema_version": "semantic-replan-request.v1", "request_id": "request-key-a",
            "task_id": "TASK-1", "rework_of": "failed-a", "workflow_run_id": "workflow-1",
            "handoff_kind": "task_base_recovery"}),
        ZfEvent(type="fanout.started", payload={"fanout_id": "a", "trigger_event_id": "request-a"}),
        ZfEvent(id="request-b", type="flow.discovery.requested", payload={
            "schema_version": "semantic-replan-request.v1", "task_id": "TASK-2",
            "workflow_run_id": "workflow-1", "handoff_kind": "task_base_recovery"}),
        ZfEvent(type="fanout.started", payload={"fanout_id": "b", "trigger_event_id": "request-b"}),
        ZfEvent(id="cancel-a", type="fanout.cancelled", payload={
            "fanout_id": "a", "superseded_by": "b", "reason": "superseded_by_latest_fanout"}),
    ]:
        log.append(event)
    assert _settle_pending_action_effects(log.read_all(), writer) == 1
    assert _settle_pending_action_effects(log.read_all(), writer) == 0
    case = build_recovery_case_projection(log.read_all())["cases"][0]
    assert case["status"] == "open"
    assert case["consecutive_failures"] == 1
    failed = [event for event in log.read_all() if event.type == "run.manager.action.effect.failed"]
    assert failed[0].payload["observed_event_id"] == "cancel-a"


def test_effect_timeout_reopens_case_once(tmp_path):
    from datetime import datetime, timedelta, timezone
    from zf.core.events.log import EventLog
    from zf.core.events.writer import EventWriter
    from zf.runtime.run_manager import _settle_pending_action_effects
    log = EventLog(tmp_path / "events.jsonl")
    writer = EventWriter(log)
    pending = _event("run.manager.action.effect.pending", "case",
                     effect_id="effect", effect_timeout_seconds=60,
                     expected_event_types=["task.done"])
    pending.ts = (datetime.now(timezone.utc) - timedelta(seconds=61)).isoformat()
    log.append(pending)
    assert _settle_pending_action_effects(log.read_all(), writer) == 1
    assert _settle_pending_action_effects(log.read_all(), writer) == 0
    case = build_recovery_case_projection(log.read_all())["cases"][0]
    assert case["status"] == "open"
    assert case["consecutive_failures"] == 1


def test_worker_effect_matches_instance_and_request(tmp_path):
    from zf.core.events.log import EventLog
    from zf.core.events.writer import EventWriter
    from zf.runtime.run_manager import _settle_pending_action_effects
    log = EventLog(tmp_path / "events.jsonl")
    writer = EventWriter(log)
    log.append(_event("run.manager.action.effect.pending", "case",
                     action="worker-lifecycle-recover", effect_id="effect",
                     instance_id="dev-0", effect_request_event_id="respawn-request",
                     expected_event_types=["worker.respawn.completed"],
                     failure_event_types=["worker.respawn.failed"]))
    log.append(ZfEvent(type="worker.respawn.completed", actor="dev-1", causation_id="respawn-request"))
    log.append(ZfEvent(type="worker.respawn.completed", actor="dev-0", causation_id="older-request"))
    assert _settle_pending_action_effects(log.read_all(), writer) == 0
    log.append(ZfEvent(type="worker.respawn.failed", actor="dev-0", causation_id="respawn-request"))
    assert _settle_pending_action_effects(log.read_all(), writer) == 1
    assert log.read_all()[-1].type == "run.manager.action.effect.failed"
