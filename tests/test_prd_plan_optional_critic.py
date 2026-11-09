"""The optional PRD plan review route remains explicit and observable."""

import pytest
import json
from types import SimpleNamespace

from zf.core.config.workflow_profiles import WorkflowProfileError, expand_prd_flow
from zf.core.events.model import ZfEvent
from zf.core.config.schema import RoleConfig, WorkflowStageConfig, FanoutAggregateConfig
from zf.runtime.plan_artifact_package_runtime import admit_task_map_trigger_package
from zf.runtime.workflow_controller_candidate import materialize_controller_candidate
from zf.core.config.loader import load_config


def test_default_prd_flow_retains_independent_plan_critic():
    result = expand_prd_flow({"lanes": 2, "backend": "mock"})
    plan = next(stage for stage in result["stages"] if stage["id"] == "prd-plan")
    assert plan["aggregate"]["synth_role"] == "plan-critic"
    assert "plan-critic" in {role["name"] for role in result["roles"]}


def test_explicit_no_critic_keeps_plan_and_downstream_stages():
    result = expand_prd_flow({"lanes": 2, "backend": "mock", "planCriticEnabled": False})
    plan = next(stage for stage in result["stages"] if stage["id"] == "prd-plan")
    assert plan["roles"] == ["planner"]
    assert not plan["aggregate"].get("synth_role")
    assert plan["aggregate"]["success_event"] == "task_map.ready"
    assert "plan-critic" not in {role["name"] for role in result["roles"]}
    assert {"prd-post-verify-discovery"} <= {stage["id"] for stage in result["stages"]}
    assert result["pipelines"]


@pytest.mark.parametrize("value", ["false", None, 0, 1])
def test_no_critic_configuration_must_be_an_explicit_boolean(value):
    with pytest.raises(WorkflowProfileError, match="planCriticEnabled"):
        expand_prd_flow({"lanes": 2, "backend": "mock", "planCriticEnabled": value})


def test_ambiguous_critic_aliases_are_rejected():
    with pytest.raises(WorkflowProfileError, match="ambiguous"):
        expand_prd_flow({"planCriticEnabled": False, "plan_critic_enabled": True})


def test_controlled_candidate_changes_only_critic_mode_and_keeps_approval(tmp_path):
    base = tmp_path / "zf.yaml"
    base.write_text("""apiVersion: zaofu.dev/v1
kind: PrdFlow
metadata: {name: sample}
spec: {lanes: 2, backend: mock}
---
apiVersion: zaofu.dev/v1
kind: ZfConfig
metadata: {name: sample}
spec:
  version: '1.0'
  project: {name: sample}
  workflow: {plan_approval: true}
""")
    before = base.read_bytes()
    path = materialize_controller_candidate(base_path=base, request_id="request-1", short_spec={
        "flow_family": "PrdFlow", "parameters": {"lanes": 2, "plan_critic_enabled": False},
    })
    assert path != base
    assert base.read_bytes() == before
    config = load_config(path)
    assert config.workflow.plan_approval_enabled
    assert not next(s for s in config.workflow.stages if s.id == "prd-plan").aggregate.synth_role
    assert len([r for r in config.roles if r.name.startswith("dev-lane-")]) == 2


def test_synthesis_admits_explicit_no_critic_parameter(tmp_path):
    from tests.test_workflow_synthesis import _context, _result
    from zf.runtime.sidecar_refs import hydrate_sidecar_ref
    from zf.runtime.workflow_synthesis import run_workflow_synthesis

    config, state, writer, projection = _context(tmp_path, kind="prd")
    result = _result(projection, "PrdFlow")
    result["short_flow_spec"]["parameters"]["plan_critic_enabled"] = False
    outcome = run_workflow_synthesis(state_dir=state, project_root=tmp_path, config=config,
        writer=writer, request_id="req-synth", actor="test", candidate_result=result)
    spec = hydrate_sidecar_ref(state, outcome.result["short_flow_spec_ref"]).payload
    assert spec["parameters"]["plan_critic_enabled"] is False


def test_no_critic_produces_reviewable_bound_config_proposal(tmp_path):
    from tests.test_workflow_proposal import _config, _request, _synthesis_result
    from zf.core.events.log import EventLog
    from zf.core.events.writer import EventWriter
    from zf.runtime.workflow_proposal import build_workflow_proposal, materialize_synthesized_workflow_candidate
    from zf.runtime.workflow_requests import load_workflow_request
    from zf.runtime.workflow_synthesis import run_workflow_synthesis

    base = tmp_path / "zf.yaml"
    _config(base)
    original = base.read_text().replace("IssueFlow", "PrdFlow").replace("issueRef", "prdRef")
    original = original.replace("spec:\n  lanes:", "spec:\n  topology: fanout\n  deliveryPolicy: report_only\n  lanes:", 1)
    base.write_text(original)
    state = tmp_path / ".zf"
    request = _request(state, requirement_fields={"kind": "prd", "target_root": "app"})
    writer = EventWriter(EventLog(state / "events.jsonl"))
    result = _synthesis_result(request, lanes=1)
    result["selected_flow_family"] = "PrdFlow"
    result["short_flow_spec"]["flow_family"] = "PrdFlow"
    result["short_flow_spec"]["parameters"]["plan_critic_enabled"] = False
    synthesis = run_workflow_synthesis(state_dir=state, project_root=tmp_path, config=load_config(base),
        writer=writer, request_id=request["request_id"], actor="test", candidate_result=result)
    request = load_workflow_request(state, request["request_id"])
    candidate = materialize_synthesized_workflow_candidate(state, request=request, base_config_path=base,
        synthesis_result_ref=synthesis.result_ref, flow_kind="prd")
    assert not next(s for s in load_config(candidate).workflow.stages if s.id == "prd-plan").aggregate.synth_role
    proposal, _ = build_workflow_proposal(state, request=request, base_config_path=base, candidate_config_path=candidate,
        synthesis_result_ref=synthesis.result_ref, flow_kind="prd", writer=writer,
        preflight={"status": "GO", "blockers": [], "delivery_contract": {"strictness": "standard"}})
    assert proposal["change_mode"] == "config_change"
    assert proposal["approval_status"] == "approvable", proposal["blockers"]
    assert base.read_text() == original


def test_planner_result_still_requires_package_admission_before_writer_dispatch(monkeypatch):
    calls = []
    def admit(runtime, **kwargs):
        calls.append(kwargs)
        return {"plan_artifact_package_ref": "artifacts/plan/package.json", "plan_artifact_package_digest": "digest"}
    monkeypatch.setattr("zf.runtime.plan_artifact_package_runtime._admit", admit)
    runtime = SimpleNamespace(config=SimpleNamespace(workflow=SimpleNamespace()))
    event = ZfEvent(type="task_map.ready", correlation_id="run-1", payload={
        "flow_kind": "prd", "stage_id": "prd-plan", "task_map_ref": "artifacts/task-map.json",
    })
    admitted = admit_task_map_trigger_package(runtime, event, [SimpleNamespace(id="prd-lanes-impl")])
    assert admitted.payload["plan_artifact_package_ref"] == "artifacts/plan/package.json"
    assert calls[0]["producer_stage_id"] == "prd-plan"
    assert calls[0]["payload"]["task_map_ref"] == "artifacts/task-map.json"


def test_failed_planner_package_admission_cannot_dispatch_writers(monkeypatch):
    cancellations = []
    def reject(runtime, **kwargs):
        raise ValueError("missing source coverage")
    monkeypatch.setattr("zf.runtime.plan_artifact_package_runtime._admit", reject)
    monkeypatch.setattr("zf.runtime.plan_artifact_package_runtime.emit_plan_admission_cancel", lambda *args, **kwargs: cancellations.append(kwargs))
    runtime = SimpleNamespace(config=SimpleNamespace(workflow=SimpleNamespace()), _fanout_pdd_id=lambda event: "goal-1")
    event = ZfEvent(type="task_map.ready", payload={"flow_kind": "prd", "task_map_ref": "artifacts/task-map.json"})
    assert admit_task_map_trigger_package(runtime, event, [SimpleNamespace(id="prd-lanes-impl")]) is None
    assert "missing source coverage" in cancellations[0]["reason"]


def test_real_blocking_package_admission_accepts_valid_planner_artifacts(tmp_path):
    from zf.core.events.log import EventLog
    from zf.core.events.writer import EventWriter
    from zf.runtime.run_contract import stable_json_sha256, write_run_contract

    state = tmp_path / ".zf"
    artifacts = state / "artifacts"
    artifacts.mkdir(parents=True)
    (artifacts / "requirement.md").write_text("Deliver AC-1.\n")
    (artifacts / "task-map.json").write_text(json.dumps({
        "schema_version": "task-map.v1", "objective": "Deliver AC-1",
        "required_plan_ports": ["requirement_spec", "goal_claim_set", "task_map", "planning_result", "acceptance_matrix"],
        "tasks": [{"task_id": "T1", "acceptance_criteria": ["AC-1"]}],
    }))
    body = {"schema_version": "run-contract.v1", "workflow": {"kind": "prd"}}
    write_run_contract(state, {**body, "contract_digest": stable_json_sha256(body)})
    log = EventLog(state / "events.jsonl")
    runtime = SimpleNamespace(
        state_dir=state, project_root=tmp_path, event_log=log, event_writer=EventWriter(log),
        config=SimpleNamespace(workflow=SimpleNamespace(flow_metadata={
            "flow_kind": "prd", "artifact_package": {"mode": "blocking"},
        })),
    )
    event = ZfEvent(type="task_map.ready", correlation_id="run-1", payload={
        "flow_kind": "prd", "stage_id": "prd-plan", "pdd_id": "GOAL-1",
        "task_map_ref": ".zf/artifacts/task-map.json", "prd_ref": ".zf/artifacts/requirement.md",
        "plan_ports": [{"logical_name": "acceptance_matrix", "schema_version": "acceptance-matrix.v1", "body": {
            "schema_version": "acceptance-matrix.v1", "status": "ready",
            "metadata": {"enrichment_contract": {"status": "fulfilled"}}, "rows": ["AC-1"],
        }}],
    })
    result = admit_task_map_trigger_package(runtime, event, [SimpleNamespace(id="prd-lanes-impl")])
    assert result is not None
    assert result.payload["plan_artifact_package_ref"]
    assert any(e.type == "plan.artifact_package.admitted" for e in log.read_all())


def test_no_critic_plan_holds_writers_until_owner_approval(tmp_path):
    from tests.test_writer_fanout_runtime import _approval_orch

    state, log, orch = _approval_orch(tmp_path, enabled=True)
    orch.config.roles.append(RoleConfig(name="planner", backend="mock", role_kind="reader"))
    orch.config.workflow.stages.append(WorkflowStageConfig(
        id="prd-plan", trigger="prd.scan.completed", topology="fanout_reader", roles=["planner"],
        result_semantics="artifact_production", aggregate=FanoutAggregateConfig(
            mode="wait_for_all", success_event="task_map.ready", failure_event="prd.plan.failed",
            child_success_event="prd.plan.child.completed", child_failure_event="prd.plan.child.failed",
        ),
    ))
    orch.run_once(events=[ZfEvent(type="prd.scan.completed", correlation_id="trace-1", payload={"pdd_id": "F-11111111"})])
    fanout = next(e.payload["fanout_id"] for e in log.read_all() if e.type == "fanout.started")
    orch.run_once(events=[ZfEvent(type="prd.plan.child.completed", actor="planner", correlation_id="trace-1", payload={
        "fanout_id": fanout, "child_id": "planner", "run_id": f"run-{fanout}-planner",
        "status": "completed", "pdd_id": "F-11111111",
        "task_map_ref": ".zf/artifacts/F-11111111/task_map.json",
        "artifact_refs": [".zf/artifacts/F-11111111/task_map.json"],
        "evidence_refs": [".zf/artifacts/F-11111111/task_map.json"],
    })])
    ready = next(e for e in log.read_all() if e.type == "task_map.ready")
    assert not any(e.type == "fanout.synth.dispatched" for e in log.read_all())
    orch.run_once(events=[ready])
    request = next(e for e in log.read_all() if e.type == "plan.approval.requested")
    assert not any(e.type == "fanout.child.dispatched" and e.payload.get("task_id") for e in log.read_all())
    approved = ZfEvent(type="plan.approved", actor="operator", correlation_id="trace-1", payload={"plan_id": request.payload["plan_id"]})
    log.append(approved)
    orch._maybe_start_writer_fanout(approved)
    assert any(e.type == "fanout.child.dispatched" and e.payload.get("task_id") for e in log.read_all())
