"""Edit scope is version-bound and never becomes an execution filter."""
import argparse
from types import SimpleNamespace

import pytest

from tests.test_control_actions_plan import _exec, _service
from tests.test_plan_artifact_package import _package
from zf.cli.plan_approval import _run
from zf.core.events.model import ZfEvent
from zf.runtime.call_result_envelope import write_immutable_json_sidecar
from zf.runtime.plan_artifact_package import write_plan_artifact_package
from zf.runtime.replan_resynth import build_replan_resynth_event
from zf.runtime.plan_synth_handoff import build_plan_handoff_input_refs
from zf.runtime.sidecar_refs import hydrate_sidecar_ref


def _pending(tmp_path):
    service, log = _service(tmp_path)
    task_map = write_immutable_json_sidecar(service.state_dir,
        {"schema_version": "task-map.v1", "tasks": [{"task_id": "T1"}, {"task_id": "T2"}]},
        root="fixtures/maps", kind="task_map", schema_version="task-map.v1", created_by="test")
    package = _package(service.state_dir)
    next(p for p in package["produced"] if p["logical_name"] == "task_map").update(
        ref=task_map["ref"], sha256=task_map["sha256"])
    descriptor = write_plan_artifact_package(service.state_dir, package)
    pending = service.writer.append(ZfEvent(type="plan.approval.requested", correlation_id="run-1", payload={
        "plan_id": "plan", "workflow_run_id": "run-1", "trace_id": "run-1", "pdd_id": "P1",
        "task_map_ref": task_map["ref"], "plan_artifact_package_ref": descriptor["ref"],
        "plan_artifact_package_digest": descriptor["sha256"],
    }))
    return service, log, pending


@pytest.mark.parametrize("surface", ["cli", "controlled"])
def test_edit_scope_reaches_bound_critic_context_without_execution_filter(tmp_path, monkeypatch, surface):
    service, log, pending = _pending(tmp_path)
    service.config.workflow.admission_replan.enabled = True
    service.config.workflow.admission_replan.resynth_trigger = "prd.scan.completed"
    if surface == "cli":
        monkeypatch.chdir(tmp_path)
        assert _run(argparse.Namespace(plan_cmd="reject", state_dir=str(service.state_dir),
            plan_id="plan", reason="Clarify T1 test details", changed_task=[" T1 ", "T1"])) == 0
    else:
        assert _exec(service, log, "plan-reject", {"plan_id": "plan", "reason": "Clarify T1 test details",
            "request_event_id": pending.id, "changed_task_ids": [" T1 ", "T1"]})["ok"]
    decision = next(e for e in log.read_all() if e.type == "plan.rejected")
    assert decision.payload["changed_task_ids"] == ["T1"]
    plan = SimpleNamespace(pdd_id="P1", trace_id="run-1", source_event_id=decision.id,
        source_event_type="plan.rejected", attempt=1, flow_kind="prd")
    trigger = build_replan_resynth_event(plan=plan, events=log.read_all(), config=service.config)
    assert trigger.payload["changed_task_ids"] == ["T1"]
    assert not trigger.payload.get("task_ids") and not trigger.payload.get("resume_scope")
    refs = build_plan_handoff_input_refs(state_dir=service.state_dir, project_root=tmp_path,
        payload={"trigger_payload": trigger.payload}, source_event_id=trigger.id)
    context = next(r for r in refs if r["source_id"] == "plan-rework-context")
    assert hydrate_sidecar_ref(service.state_dir, context).payload["changed_task_ids"] == ["T1"]


@pytest.mark.parametrize("problem", [None, [], "T1", [1], [" "], ["UNKNOWN"], "tampered", "stale"])
def test_invalid_edit_scope_preserves_pending_decision(tmp_path, problem):
    service, log, pending = _pending(tmp_path)
    scope = problem
    if problem == "tampered":
        (service.state_dir / pending.payload["task_map_ref"]).write_text('{"tasks":[]}')
        scope = ["T1"]
    result = _exec(service, log, "plan-reject", {
        "plan_id": "plan", "reason": "Clarify", "changed_task_ids": scope,
        "request_event_id": "obsolete-request" if problem == "stale" else pending.id,
    })
    assert not result["ok"]
    assert not any(e.type == "plan.rejected" for e in log.read_all())


def test_fresh_unscoped_replan_drops_previous_edit_scope(tmp_path):
    service, log, _ = _pending(tmp_path)
    service.config.workflow.admission_replan.enabled = True
    service.config.workflow.admission_replan.resynth_trigger = "prd.scan.completed"
    service.writer.append(ZfEvent(type="prd.scan.completed", correlation_id="run-1", payload={
        "workflow_run_id": "run-1", "changed_task_ids": ["T1"]}))
    assert _exec(service, log, "plan-reject", {"plan_id": "plan", "reason": "Review all"})["ok"]
    decision = next(e for e in log.read_all() if e.type == "plan.rejected")
    trigger = build_replan_resynth_event(plan=SimpleNamespace(pdd_id="P1", trace_id="run-1",
        source_event_id=decision.id, source_event_type="plan.rejected", attempt=1, flow_kind="prd"),
        events=log.read_all(), config=service.config)
    assert "changed_task_ids" not in trigger.payload
