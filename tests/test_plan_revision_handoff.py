"""The team workbench and the idle detector must describe the same revision."""

from zf.core.events.model import ZfEvent
from zf.runtime.stage_team_projection import project_stage_teams, team_summary


RUN = "r-1"
PACKAGE = "a" * 64


def _chain(run=RUN):
    events = []

    def emit(kind, *, cause=None, **payload):
        event = ZfEvent(type=kind, origin="kernel", actor="zf-cli", causation_id=cause,
                        correlation_id=run, payload={"workflow_run_id": run, **payload})
        events.append(event)
        return event

    ready = emit("task_map.ready", plan_artifact_package_digest=PACKAGE, task_map_generation="g", stage_id="prd-plan")
    submitted = emit("orchestrator.semantic.decision.submitted")
    bound = dict(checkpoint="pre_impl", checkpoint_policy="blocking", decision="revise", source_event_id=ready.id,
                 decision_event_id=submitted.id, operation_id="oa-call", decision_ref={"ref":"oa", "sha256":"b" * 64})
    admitted = emit("orchestrator.semantic.decision.admitted", cause=submitted.id, **bound)
    emit("orchestrator.semantic.decision.applied", cause=submitted.id, status="escalated", applied=False, **bound)
    trigger = emit("prd.scan.completed", cause=admitted.id, orchestrator_decision_event_id=submitted.id,
                   revision_target_role="planner", replan_classification="oa_pre_impl_revision",
                   plan_artifact_package_digest=PACKAGE, task_map_generation="g")
    emit("fanout.started", trigger_event_id=trigger.id, stage_id="prd-plan", fanout_id="f")
    emit("fanout.child.dispatched", fanout_id="f", role_instance="planner", operation_id="new-plan-call")
    return events


def _project(events):
    stage = {"stage_id":"prd-plan", "definition":{}}
    nodes = {"workflow:stage:prd-plan": {"id":"workflow:stage:prd-plan", "data":stage}}
    scope = {"run_id":RUN, "request_id":"q", "revision":1}
    project_stage_teams(nodes, {"scope":scope, "events":list(enumerate(events, 1)), "compiled":{}}, {})
    return team_summary(stage["team"]).get("revision_handoff", {})


def test_workbench_only_observes_delivery_after_matching_receipt():
    rows = _chain()
    assert _project(rows[:2]) == {}
    assert _project(rows[:3])["status"] == "awaiting_route"
    decision = _project(rows[:4])
    assert decision["status"] == "awaiting_owner" and decision["package_digest"] == PACKAGE
    assert "obligation_id" not in decision and decision["sender_operation_id"] == "oa-call"
    for n in (5, 6):
        assert _project(rows[:n])["status"] == "awaiting_dispatch"
        assert _project(rows[:n])["obligation_id"] == "plan-author"
    dispatched = _project(rows)
    assert dispatched["dispatch_event_id"] == rows[6].id and dispatched["operation_id"] == "new-plan-call"
    assert dispatched["target_role"] == "planner" and dispatched["status"] == "dispatched"
    assert _project(rows + _chain("foreign")) == dispatched


def test_foreign_unbound_or_superseded_receipts_cannot_claim_revision():
    rows = _chain()
    wrong = rows[-1]
    wrong.payload["role_instance"] = "other-reader"
    assert _project(rows)["status"] == "awaiting_dispatch"
    wrong.payload["role_instance"] = "planner"
    wrong.origin = "worker"
    assert _project(rows)["status"] == "awaiting_dispatch"
    changed = ZfEvent(type="plan.artifact_package.admitted", origin="kernel", correlation_id=RUN,
                      payload={"workflow_run_id":RUN, "package_digest":"c" * 64, "task_map_generation":"next"})
    assert _project([*rows, changed]) == {}
    owner = ZfEvent(type="plan.approved", actor="operator", origin="kernel", payload={"plan_id":rows[0].id})
    assert _project([*rows, owner]) == {}
    owner.payload["workflow_run_id"] = "another-run"
    assert _project([*rows, owner]) == _project(rows)


def test_goal_idle_reports_exact_missing_handoff_and_honors_owner_pause():
    from tests.test_goal_idle_driver import _cfg, _goal_started, _state, _tick_until_fire, _Writer

    rows = [_goal_started(), *_chain()[:5]]
    writer = _Writer()
    assert _tick_until_fire(rows, _cfg(), _state(), writer) == "rescan"
    observed = writer.appended[0].payload["revision_handoff"]
    assert observed["status"] == "awaiting_dispatch" and observed["trigger_event_id"] == rows[-1].id
    rows.append(ZfEvent(type="run.paused", origin="kernel", correlation_id=RUN, payload={"workflow_run_id":RUN}))
    assert _project(rows)["paused"] is True
    assert _tick_until_fire(rows, _cfg(), _state(), _Writer()) == ""
