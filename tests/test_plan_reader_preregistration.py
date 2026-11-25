"""Plan Reader preregistration failures carry no semantic replan charge."""

from dataclasses import replace

import pytest

from tests.test_reader_fanout_runtime import _RecordingTransport
from zf.core.config.schema import (FanoutAggregateConfig, FanoutChildConfig, ProjectConfig,
                                   RoleConfig, WorkflowConfig, WorkflowStageConfig, ZfConfig)
from zf.core.events import ZfEvent
from zf.runtime.orchestrator import Orchestrator
from zf.runtime.plan_reader_preregistration import plan_failure_kind
from zf.runtime.reader_failure_repair import repaired_failure_kind
from zf.runtime.stage_failure_replan import plan_reader_stage_replan


@pytest.fixture
def prereg(tmp_path, monkeypatch):
    state = tmp_path / "state"
    state.mkdir()
    config = ZfConfig(project=ProjectConfig(name="test", state_dir=str(state)),
        roles=[RoleConfig(name=r, backend="mock", role_kind="reader") for r in ("planner", "plan-critic")],
        workflow=WorkflowConfig(stages=[WorkflowStageConfig(id="prd-plan", topology="fanout_reader",
            trigger="prd.scan.completed", attempt_domain="plan",
            children=[FanoutChildConfig(role_instance="planner")],
            aggregate=FanoutAggregateConfig(synth_role="plan-critic", success_event="task_map.ready",
                failure_event="prd.plan.failed", child_success_event="prd.plan.child.completed",
                child_failure_event="prd.plan.child.failed"))]))
    worker = _RecordingTransport()
    runtime = Orchestrator(state, config, worker, project_root=tmp_path)
    def unavailable(**_kwargs):
        raise ValueError("fixture unavailable")

    monkeypatch.setattr(runtime, "_prepare_reader_fanout_child_operation", unavailable)
    trigger = runtime.event_writer.emit("prd.scan.completed", correlation_id="R",
                                        payload={"workflow_run_id": "R", "status": "completed"})
    runtime.run_once(events=[trigger])
    child = next(e for e in runtime.event_log.read_all() if e.type == "fanout.child.failed")
    runtime._recover_incomplete_reader_fanout_aggregates(runtime.event_log.read_all())
    events = runtime.event_log.read_all()
    failure = next(e for e in events if e.type == "prd.plan.failed")
    return runtime, events, child, failure


def test_plan_reader_preregistration_routes_without_plan_rejection(prereg):
    runtime, events, child, failure = prereg
    assert failure.payload["failure_class"] == "reader_execution_failure"
    assert failure.payload["failure_kind"] == "infra"
    assert failure.payload["recommendation"] == "abstain"
    assert child.id in str(failure.payload["findings"])
    assert not runtime.transport.sent
    assert not any(e.type == "workflow.operation.requested" for e in events)
    assert plan_reader_stage_replan(runtime.config, events, failure)[1] == "infrastructure_failure"


def legacy(events, failure):
    start = next(e for e in events if e.type == "fanout.started")
    descriptor = {"kind": "plan_candidate_preflight", "ref": "artifacts/preflight.json",
                  "sha256": "a" * 64, "source_event_id": start.causation_id}
    rows = []
    for e in events:
        if e.type in {"fanout.synth.completed", "fanout.aggregate.completed", "prd.plan.failed"}:
            body = {**e.payload, "failure_class": "plan_candidate_preflight", "recommendation": "reject",
                    "plan_candidate_preflight_ref": descriptor}
            body.pop("failure_kind", None)
            e = replace(e, payload=body)
        rows.append(e)
    return rows, next(e for e in rows if e.id == failure.id)


@pytest.mark.parametrize("fault", ["", "roster", "origin", "run", "trigger", "class", "operation",
                                   "dispatch", "verdict", "synth_binding", "mixed", "unknown",
                                   "foreign_preflight_ref", "concurrent_work", "signed_semantic_result",
                                   "other_synth", "readers_product_result"])
def test_historical_plan_preflight_requires_unanimous_unissued_child_evidence(prereg, fault):
    runtime, original, child, failure = prereg
    rows, failure = legacy(original, failure)
    start = next(e for e in rows if e.type == "fanout.started")
    if fault == "roster":
        rows[rows.index(start)] = replace(start, payload={**start.payload,
            "expected_children": [*start.payload["expected_children"], {"child_id": "other", "role_instance": "other"}]})
    elif fault == "trigger":
        rows[rows.index(start)] = replace(start, causation_id="other-trigger")
    elif fault == "origin":
        rows[rows.index(child)] = replace(child, origin="agent")
    elif fault in {"run", "class", "verdict"}:
        fields = {"run": {"workflow_run_id": "foreign"}, "class": {"failure_scope": "unknown"},
                  "verdict": {"control_result_ref": {"ref": "accepted.json"}}}
        rows[rows.index(child)] = replace(child, payload={**child.payload, **fields[fault]})
    elif fault == "operation":
        rows.insert(rows.index(child), ZfEvent(type="workflow.operation.requested", origin="kernel",
            payload={"active_attempt_id": child.payload["run_id"], "workflow_run_id": "R"}))
    elif fault == "concurrent_work":
        rows.insert(rows.index(child), ZfEvent(type="workflow.operation.requested", origin="kernel",
            payload={"parent_stage_id": start.payload["stage_id"], "workflow_run_id": "R"}))
    elif fault in {"other_synth", "readers_product_result"}:
        rows.insert(rows.index(child), ZfEvent(type=("fanout.synth.completed" if fault == "other_synth"
            else "prd.plan.child.completed"), origin="kernel", payload={"fanout_id": child.payload["fanout_id"],
                "report": {"recommendation": "reject"}}))
    elif fault == "signed_semantic_result":
        rows.insert(rows.index(child), ZfEvent(type="workflow.call.result.admitted", origin="kernel",
            payload={"attempt_id": child.payload["run_id"], "semantic_verdict": "rejected"}))
    elif fault in {"dispatch", "mixed"}:
        rows.insert(rows.index(child), replace(child, id="other", type={
            "dispatch": "fanout.child.dispatched", "mixed": "fanout.child.completed"}[fault]))
    elif fault == "synth_binding":
        synth = next(e for e in rows if e.id == failure.causation_id)
        rows[rows.index(synth)] = replace(synth, causation_id="foreign-trigger")
    elif fault == "foreign_preflight_ref":
        synth = next(e for e in rows if e.id == failure.causation_id)
        rows[rows.index(synth)] = replace(synth, payload={**synth.payload, "plan_candidate_preflight_ref": {}})
    elif fault == "unknown":
        rows.insert(0, ZfEvent(type="event.malformed", payload={"ledger_integrity": "unknown"}))
    expected = "" if fault else "infra"
    assert plan_failure_kind(rows, failure) == expected
    assert repaired_failure_kind(runtime.state_dir, rows, failure) == expected
    if not fault:
        assert plan_reader_stage_replan(runtime.config, rows, failure)[1] == "infrastructure_failure"
