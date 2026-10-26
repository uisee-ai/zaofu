from __future__ import annotations

import json

import pytest

from zf.core.events.model import ZfEvent
from zf.runtime.goal_closure_identity import build_closure_identity
from zf.runtime.goal_closure_lifecycle_evidence import plan_lifecycle_evidence


PACKAGE = {"plan_artifact_package_digest": "d" * 64}


def event(kind, **body):
    return ZfEvent(type=kind, payload={"workflow_run_id": "run-1", **body})


def fixture_events():
    request = event("plan.approval.requested", plan_id="plan-1", **PACKAGE)
    approval = ZfEvent(type="plan.approved", actor="operator", payload={"plan_id": "plan-1"})
    dispatch = event("task.pipeline.stage.dispatched", task_map_generation="gen-1",
                     task_pipeline_stage="impl", task_id="T1", dispatch_id="dispatch-1", **PACKAGE)
    return [request, approval, dispatch]


def project(events):
    return plan_lifecycle_evidence(events, workflow_run_id="run-1",
                                   task_map_generation="gen-1", package_identity=PACKAGE)


def test_package_bound_operator_approval_and_dispatch_preserve_order():
    events = fixture_events()
    rows = project(events)
    assert [r["event_id"] for r in rows] == [e.id for e in events]
    assert [r["ledger_position"] for r in rows] == [0, 1, 2]
    assert rows[1]["actor"] == "operator"
    assert rows[2]["payload"]["dispatch_id"] == "dispatch-1"
    # A late approval is evidence of the actual ordering, never repaired.
    assert [r["event_type"] for r in project([events[0], events[2], events[1]])] == [
        "plan.approval.requested", "task.pipeline.stage.dispatched", "plan.approved",
    ]


@pytest.mark.parametrize("change", [
    {"workflow_run_id": "other-run"},
    {"plan_artifact_package_digest": "old-package"},
])
def test_other_run_or_package_cannot_supply_approval(change):
    events = fixture_events()
    events[0].payload.update(change)
    assert [r["event_type"] for r in project(events)] == ["task.pipeline.stage.dispatched"]


def test_stale_dispatch_and_semantic_adoption_are_excluded():
    events = fixture_events()
    events[1].payload["semantic_control"] = True
    events[2].payload["task_map_generation"] = "old-generation"
    assert [r["event_type"] for r in project(events)] == ["plan.approval.requested"]
    assert project([]) == []


def test_unscoped_decision_binds_request_but_conflicting_run_does_not():
    events = fixture_events()
    events[1].payload["workflow_run_id"] = "other-run"
    assert len(project(events)) == 2
    events[1].payload.pop("workflow_run_id")
    events[1].type = "plan.rejected"
    assert project(events)[1]["event_type"] == "plan.rejected"


def test_identity_binds_lifecycle_facts_and_replays_stably(tmp_path):
    events = [event("plan.artifact_package.admitted", package_slot="execution_plan",
                    task_map_generation="gen-1", package_id="planpkg-1",
                    package_ref="artifacts/package.json", package_digest="d" * 64,
                    mode="blocking"), *fixture_events()]
    source = event("flow.discovery.completed")
    payload = {"workflow_run_id": "run-1", "goal_id": "goal-1",
               "task_map_generation": "gen-1", "candidate_head_commit": "a" * 40}
    def build(rows):
        return build_closure_identity(rows, source_event=source, payload=payload,
                                      state_dir=tmp_path, flow_kind="prd")
    identity = build(events)
    facts = json.loads((tmp_path / identity["closure_fact_ref"]).read_text())
    assert len(facts["plan_lifecycle_evidence"]) == 3
    assert build([*events, event("agent.usage")])["closure_identity"] == identity["closure_identity"]
    assert build([events[0], events[1], events[3]])["closure_identity"] != identity["closure_identity"]


def test_runtime_read_contract_requires_whole_closure_fact(tmp_path):
    from zf.core.config.schema import ZfConfig
    from zf.runtime.goal_closure_bridge import GoalClosureBridgeMixin
    from zf.runtime.call_result_envelope import write_immutable_json_sidecar

    facts = write_immutable_json_sidecar(
        tmp_path, {"plan_lifecycle_evidence": project(fixture_events())},
        root="goal-closure/facts", kind="goal_closure_fact",
        schema_version="closure-source.v1", created_by="test",
    )
    bridge = GoalClosureBridgeMixin()
    bridge.state_dir = tmp_path
    bridge.project_root = tmp_path
    bridge.config = ZfConfig()
    contract = bridge._goal_closure_read_contract(
        [], workflow_run_id="run-1", goal_id="goal-1",
        identity={"closure_fact_ref": facts["ref"], "closure_fact_digest": facts["sha256"]},
        claim_set={}, payload={},
    )
    required = next(row for row in contract["required_reads"] if row["source_id"] == "closure-fact")
    assert required["json_path"] == "$"
    assert required["artifact_sha256"] == facts["sha256"]
    descriptor = next(row for row in contract["input_refs"] if row["source_id"] == "closure-fact")
    assert descriptor["ref"] == facts["ref"]
