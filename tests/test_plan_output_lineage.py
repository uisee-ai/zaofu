"""New direct Plan outputs cannot reuse the identity of their input package."""

import json
from pathlib import Path

import pytest

from zf.core.events.model import ZfEvent
from zf.core.task.schema import Task, TaskContract
from zf.core.task.store import TaskStore
from zf.runtime.call_result_envelope import write_immutable_json_sidecar
from zf.runtime.plan_artifact_package import (
    PlanArtifactPackageError, admit_plan_artifact_package_for_payload,
    package_event_payload, write_plan_artifact_package,
)
from zf.runtime.plan_output_lineage import package_task_map
from tests.test_control_actions_recovery import _service, _req
from tests.test_plan_artifact_package import _package
from tests.test_reader_fanout_runtime import _state


def _fixture(tmp_path):
    svc = _service(tmp_path)
    package = _package(svc.state_dir)
    descriptor = write_plan_artifact_package(svc.state_dir, package)
    svc.writer.append(ZfEvent(type="plan.artifact_package.admitted", origin="kernel",
                             payload=package_event_payload(package, descriptor, status="admitted")))
    task_map = {"tasks": [{"task_id": "T1"}, {"task_id": "T2"}]}
    path = svc.state_dir / "artifacts" / "new-map.json"
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(task_map))
    source = svc.writer.append(ZfEvent(type="task_map.ready", origin="kernel", payload={
        "workflow_run_id": "run-1", "task_map_ref": "artifacts/new-map.json",
        "task_map_generation": "g1", "plan_artifact_package_ref": descriptor["ref"],
        "plan_artifact_package_digest": descriptor["sha256"],
        "plan_ports": [{"logical_name": "task_execution_input", "body": {"full": True}}],
    }))
    return svc, source, package


def test_admission_rejects_same_generation_different_task_map(tmp_path):
    svc, source, package = _fixture(tmp_path)
    kwargs = dict(state_dir=svc.state_dir, project_root=tmp_path, event_writer=svc.writer,
                  events=svc.writer.event_log.read_all(), payload=source.payload,
                  workflow_run_id="run-1", flow_kind="prd", producer_stage_id="plan", goal_id="G",
                  metadata={"artifact_package": {"mode": "blocking"}})
    with pytest.raises(PlanArtifactPackageError, match="different canonical content"):
        admit_plan_artifact_package_for_payload(**kwargs)
    (svc.state_dir / "artifacts/new-map.json").write_text(json.dumps(package_task_map(svc.state_dir, package)))
    # The fixture's new unbound input is a separate change, not an exact replay.
    source.payload.pop("plan_ports")
    identity = admit_plan_artifact_package_for_payload(**kwargs)
    assert identity["plan_artifact_package_ref"] == source.payload["plan_artifact_package_ref"]


def test_direct_reader_aggregate_clears_input_identity(tmp_path, monkeypatch):
    _, log, _, orch = _state(tmp_path)
    manifest = {
        "fanout_id": "f-plan", "stage_id": "plan", "trace_id": "run-1",
        "pdd_id": "P1", "children": [{"child_id": "planner", "status": "completed"}],
        "aggregate_config": {"success_event": "task_map.ready"},
        "trigger_payload": {"task_map_ref": "old.json", "task_map_generation": "g-old",
                            "plan_artifact_package_ref": "old-package.json"},
    }
    monkeypatch.setattr(orch, "_fanout_manifest", lambda _: manifest)
    monkeypatch.setattr(orch, "_fanout_identity_stale_reason", lambda _: ("", ""))
    monkeypatch.setattr(orch, "_generic_fanout_success_payload", lambda **_: {
        "task_map_ref": "new.json", "source_index_ref": "source.json",
        "implementation_plan_ref": "plan.md", "task_map_generation": "g-old",
        "plan_artifact_package_ref": "old-package.json"})
    orch._evaluate_reader_fanout("f-plan")
    ready = [e for e in log.read_all() if e.type == "task_map.ready"]
    assert len(ready) == 1
    assert ready[0].payload["task_map_ref"] == "new.json"
    assert "task_map_generation" not in ready[0].payload
    assert "plan_artifact_package_ref" not in ready[0].payload


def _completed_reopened(svc, source):
    store = TaskStore(svc.state_dir / "kanban.json")
    task = Task(id="KEPT", status="backlog", contract=TaskContract(verification="pytest"),
                contract_authority_revision="old-revision")
    store.add(task)
    store.update("KEPT", status="done", completed_at="2026-11-05T00:00:00+00:00")
    receipt = write_immutable_json_sidecar(svc.state_dir, {"task_id": "KEPT", "workflow_run_id": "run-1",
        "status": "integrated", "new_candidate_head": "accepted-commit"},
        root="artifacts/task-integration-receipts", kind="task_integration_receipt",
        schema_version="task-integration-receipt.v1", created_by="test")
    done = svc.writer.append(ZfEvent(type="task.done", origin="kernel", task_id="KEPT", payload={
        "workflow_run_id": "run-1", "candidate_head": "accepted-commit", "integration_receipt_ref": receipt}))
    task.contract_authority_revision = "new-revision"
    store.reopen(task)
    svc.writer.append(ZfEvent(type="task.contract.revision.applied", origin="kernel", task_id="KEPT",
        payload={"source": "refactor_replan_adoption", "reopened_from_terminal": True,
                 "new_task_map_ref": source.payload["task_map_ref"],
                 "previous_authority_revision": "old-revision",
                 "contract_authority_revision": "new-revision"}))
    (svc.state_dir / "artifacts/new-map.json").write_text(json.dumps({
        "tasks": [{"task_id": "T1"}, {"task_id": "T2"}, {"task_id": "KEPT"}]}))
    return store, done


def _repair(svc, source, **overrides):
    return svc.execute(action="stage-retrigger", requested_action="stage-retrigger", requested=_req({}),
        payload={"source_event_id": source.id, "repair_plan_output": True,
                 "approval_ref": "owner-review", **overrides})


def test_controlled_repair_retains_original_completion_and_full_map(tmp_path):
    svc, source, _ = _fixture(tmp_path)
    store, done = _completed_reopened(svc, source)
    original = (svc.state_dir / "artifacts/new-map.json").read_bytes()
    result = _repair(svc, source, retain_completed_task_ids=["KEPT"])
    assert result["ok"], result
    ready = next(e for e in svc.writer.event_log.read_all() if e.id == result["retriggered_event_id"])
    assert ready.payload["task_ids"] == ["T1", "T2"]
    assert ready.payload["plan_ports"] == source.payload["plan_ports"]
    assert "plan_artifact_package_ref" not in ready.payload
    assert ready.payload["plan_output_recovery"]["retained_completion_events"] == {"KEPT": done.id}
    assert store.get("KEPT").status == "done"
    assert (svc.state_dir / "artifacts/new-map.json").read_bytes() == original
    assert not _repair(svc, source, retain_completed_task_ids=["KEPT"])["ok"]
    assert len([e for e in svc.writer.event_log.read_all() if e.type == "task.done" and e.task_id == "KEPT"]) == 1


def test_repair_resumes_after_completion_cas_before_receipt(tmp_path, monkeypatch):
    svc, source, _ = _fixture(tmp_path)
    store, done = _completed_reopened(svc, source)
    append = svc.writer.append

    def crash(event):
        if event.type == "task.status_changed":
            raise OSError("simulated crash after TaskStore CAS")
        return append(event)

    monkeypatch.setattr(svc.writer, "append", crash)
    failed = _repair(svc, source, retain_completed_task_ids=["KEPT"])
    assert not failed["ok"]
    assert store.get("KEPT").status == "done"
    monkeypatch.setattr(svc.writer, "append", append)
    result = _repair(svc, source, retain_completed_task_ids=["KEPT"])
    assert result["ok"], result
    receipts = [e for e in svc.writer.event_log.read_all() if e.type == "task.status_changed"]
    assert receipts[-1].payload["completion_event_id"] == done.id


@pytest.mark.parametrize("case", ["approval", "later_attempt", "changed_acceptance", "unknown_task", "wrong_run"])
def test_repair_rejects_unproven_retention_before_mutation(tmp_path, case):
    svc, source, _ = _fixture(tmp_path)
    store, _ = _completed_reopened(svc, source)
    overrides = {"retain_completed_task_ids": ["KEPT"]}
    if case == "approval":
        overrides["approval_ref"] = ""
    elif case == "later_attempt":
        svc.writer.append(ZfEvent(type="task.dispatched", task_id="KEPT", origin="kernel"))
    elif case == "changed_acceptance":
        changed = store.get("KEPT")
        changed.contract.verification = "different command"
        store.reopen(changed)
    elif case == "unknown_task":
        overrides["retain_completed_task_ids"] = ["KEPT", "MISSING"]
    elif case == "wrong_run":
        source.payload["workflow_run_id"] = "other-run"
        # The action resolves source evidence from the ledger, not caller objects.
        source = svc.writer.append(ZfEvent(type="task_map.ready", origin="kernel", payload=source.payload))
    result = _repair(svc, source, **overrides)
    assert not result["ok"], result
    assert store.get("KEPT").status == "backlog"
