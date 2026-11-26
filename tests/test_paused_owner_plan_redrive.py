from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from tests.test_writer_fanout_runtime import _state
from zf.core.config.schema import FanoutAggregateConfig, WorkflowStageConfig
from zf.core.events import ZfEvent
from zf.web.server import create_app

URL = "/api/actions/stage-replan-new-generation"
HEADERS = {"x-zf-web-token": "test-token"}


@pytest.fixture
def plan(tmp_path, monkeypatch):
    monkeypatch.setenv("ZF_WEB_ACTION_TOKEN", "test-token")
    monkeypatch.delenv("ZF_WEB_TRUSTED_SESSION", raising=False)
    state, log, _, runtime = _state(tmp_path)
    runtime.config.workflow.stages.append(
        WorkflowStageConfig(
            id="review-plan",
            trigger="prd.scan.completed",
            topology="fanout_reader",
            attempt_domain="plan",
            roles=["supervisor"],
            aggregate=FanoutAggregateConfig(failure_event="prd.plan.failed"),
        )
    )
    api = TestClient(create_app(state, config=runtime.config, project_root=tmp_path))
    run = "inventory-import"
    base = {"workflow_run_id": run, "pdd_id": "inventory", "stage_id": "review-plan"}

    def emit(kind, body=None, *, cause=None, actor="zf-cli", origin="kernel"):
        return runtime.event_writer.append(
            ZfEvent(
                type=kind,
                actor=actor,
                origin=origin,
                task_id="inventory",
                correlation_id=run,
                causation_id=cause,
                payload={**base, **(body or {})},
            )
        )

    emit("run.goal.started", {"run_id": run, "status": "active"})
    emit("prd.scan.completed", {"input_task_ids": ["inventory-schema"]})
    failure = emit("prd.plan.failed")
    cap = emit(
        "human.escalate",
        {
            "failure_class": "stage_replan_cap_exhausted",
            "source_event_id": failure.id,
            "allowed_actions": ["start_new_generation"],
        },
        actor="orchestrator",
        cause=failure.id,
    )
    auth = {
        "escalation_event_id": cap.id,
        "workflow_run_id": run,
        "approval_ref": "owner:inventory-v2",
        "operator_feedback": ["Retain the agreed input schema."],
    }
    opened = api.post(URL, headers=HEADERS, json=auth)
    assert opened.status_code == 200, opened.text
    original = next(e for e in log.read_all() if e.id == opened.json()["stage_trigger_event_id"])
    emit(
        "fanout.started", {"trigger_event_id": original.id, "fanout_id": "owner-fanout"}, cause=original.id
    )
    emit("fanout.child.dispatched", {"fanout_id": "owner-fanout", "operation_id": "owner-op"})
    old = emit(
        "prd.scan.completed",
        {"stage_replan_generation": "prior-inventory", "rework_of": "previous-business-ready"},
        cause=failure.id,
    )
    emit("fanout.started", {"trigger_event_id": old.id, "fanout_id": "retired-fanout"}, cause=old.id)
    emit(
        "workflow.operation.cancelled",
        {"operation_id": "owner-op", "reason": "superseded_reader_fanout: superseded_by_latest_fanout"},
    )
    pause = api.post("/api/actions/run-pause", headers=HEADERS, json={"workflow_run_id": run})
    assert pause.status_code == 200, pause.text
    params = {k: v for k, v in auth.items() if k != "operator_feedback"}
    return api, log, emit, original, {**params, "redrive_owner_trigger_event_id": original.id}


def test_parallel_public_requests_reuse_single_frozen_checkpoint(plan):
    api, log, emit, original, body = plan
    # A local result of the retired branch is still audit evidence. Only the business frontier can adopt it.
    emit("fanout.child.completed", {"fanout_id": "retired-fanout"})
    emit("fanout.cancelled", {"fanout_id": "retired-fanout", "source": "authorized_plan_recovery_frontier"})
    with ThreadPoolExecutor(max_workers=2) as executor:
        responses = list(
            executor.map(
                lambda reason: api.post(URL, headers=HEADERS, json={**body, "reason": reason}),
                ["first owner request", "second request"],
            )
        )
    assert [r.status_code for r in responses] == [200, 200], [r.text for r in responses]
    assert sorted(r.json()["replayed"] for r in responses) == [False, True]
    ids = {r.json()["event_id"] for r in responses}
    redrives = [e for e in log.read_all() if e.payload.get("redrive_of") == original.id]
    assert ids == {redrives[0].id} and len(redrives) == 1
    assert redrives[0].payload == {**original.payload, "redrive_of": original.id, "rework_of": original.id}


@pytest.mark.parametrize(
    "condition",
    [
        "no_token",
        "other_run",
        "wrong_approval",
        "wrong_escalation",
        "wrong_source",
        "scope",
        "unpaused",
        "forged_pause",
        "approved",
        "owner_delivered",
        "newer_owner",
        "new_successor",
    ],
)
def test_refuses_unbound_or_obsolete_recovery(plan, condition):
    api, log, emit, original, body = plan
    if condition == "unpaused":
        emit("run.resumed")
    elif condition == "forged_pause":
        emit("run.resumed")
        emit("run.paused", origin="worker", actor="planner")
    elif condition == "approved":
        emit("plan.approved", {"plan_id": "admitted-plan"})
    elif condition == "owner_delivered":
        emit("fanout.child.completed", {"fanout_id": "owner-fanout"})
    elif condition == "newer_owner":
        emit("run.goal.updated", {"stage_replan_generation": "another-owner-generation"})
        emit("run.paused")
    elif condition == "new_successor":
        emit("fanout.started", {"trigger_event_id": "unrelated-trigger", "fanout_id": "current-fanout"})
    changes = {
        "other_run": {"workflow_run_id": "other"},
        "wrong_approval": {"approval_ref": "unrelated"},
        "wrong_escalation": {"escalation_event_id": "stale"},
        "wrong_source": {"redrive_owner_trigger_event_id": "missing"},
        "scope": {"operator_feedback": ["supplant owner"]},
    }
    result = api.post(
        URL, headers={} if condition == "no_token" else HEADERS, json={**body, **changes.get(condition, {})}
    )
    assert result.status_code == (403 if condition == "no_token" else 409), result.text
    assert not any(e.payload.get("redrive_of") == original.id for e in log.read_all())


def test_interrupted_before_action_receipt_reuses_published_trigger(plan, monkeypatch):
    from zf.runtime.control_actions_recovery import RecoveryActionsMixin

    api, log, _, original, body = plan
    normal = RecoveryActionsMixin._recovery_ok

    def lose_receipt(service, requested, marker, action, requested_action, extra):
        if marker.payload.get("redrive_of") == original.id:
            raise RuntimeError("simulated process stop after trigger append")
        return normal(service, requested, marker, action, requested_action, extra)

    with monkeypatch.context() as local:
        local.setattr(RecoveryActionsMixin, "_recovery_ok", lose_receipt)
        with pytest.raises(RuntimeError, match="simulated process stop"):
            api.post(URL, headers=HEADERS, json=body)
    retry = api.post(URL, headers=HEADERS, json=body)
    assert retry.status_code == 200 and retry.json()["replayed"], retry.text
    assert sum(e.payload.get("redrive_of") == original.id for e in log.read_all()) == 1
