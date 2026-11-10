"""Product ingest preserves declarations consumed by the real input reader."""

from copy import deepcopy

import pytest

from zf.core.task.store import TaskStore
from zf.runtime.artifact_read_ledger import ArtifactReadError
from zf.runtime.call_result_envelope import write_immutable_json_sidecar
from zf.runtime.plan_artifact_package import build_plan_artifact_package, write_plan_artifact_package
from zf.runtime.product_delivery import ingest_task_map_to_kanban
from zf.runtime.run_contract import stable_json_sha256, write_run_contract_snapshot
from zf.runtime.task_scoped_plan_inputs import current_plan_port_sources
from tests.test_product_delivery import _task_map


GLOBAL = ["task_map", "execution_spec", "task_execution_input", "acceptance_matrix", "test_matrix", "real_e2e_matrix"]
TASK = "TASK-PROD-A"


def _map(*, global_ports=None, local_ports=None, selection=False):
    body = _task_map()
    if global_ports is not None:
        body["required_plan_ports"] = global_ports
    evidence = {}
    if local_ports is not None:
        evidence["required_plan_ports"] = local_ports
    if selection:
        evidence["plan_input_selection"] = {
            "schema_version": "task-plan-input-selection.v1",
            "implementation": ["task_execution_input", "local_detail"],
            "task-verify": ["task_execution_input", "local_detail"],
        }
    body["tasks"][0]["evidence_contract"] = evidence
    return body


@pytest.mark.parametrize(("global_ports", "local_ports", "expected"), [
    (["task_execution_input"], ["local_detail"], ["task_execution_input", "local_detail"]),
    (["task_execution_input", "local_detail"], ["local_detail", "more_detail"],
     ["task_execution_input", "local_detail", "more_detail"]),
    (["prd_ref"], ["requirement_spec", "local_detail"], ["requirement_spec", "local_detail"]),
    (["task_execution_input"], None, ["task_execution_input"]),
    (None, ["local_detail"], ["local_detail"]),
    (None, None, None),
])
def test_ingest_keeps_global_and_local_required_ports(tmp_path, global_ports, local_ports, expected):
    body = _map(global_ports=global_ports, local_ports=local_ports)
    before = deepcopy(body)
    result = ingest_task_map_to_kanban(tmp_path, body)
    assert result.passed, result.errors
    task = TaskStore(tmp_path / "kanban.json").get(TASK)
    assert task.contract.evidence_contract.get("required_plan_ports") == expected
    assert body == before


def _bind_package(state, body, *, missing=()):
    names = list(dict.fromkeys(["task_map", *GLOBAL, "local_detail", "local_extra"]))
    ports = []
    for name in names:
        if name in missing:
            continue
        payload = body if name == "task_map" else {
            "schema_version": "task-scoped-plan-input.v1", "shared": {"policy": "original"},
            "tasks": [{"task_id": task["task_id"], "body": {"detail": name}}
                      for task in body["tasks"]],
        }
        ref = write_immutable_json_sidecar(state, payload, root="fixtures", kind=name,
            schema_version=payload["schema_version"], created_by="test")
        ports.append({"logical_name": name, "artifact_kind": name,
            "schema_version": payload["schema_version"], "producer_stage_id": "plan",
            "ref": ref["ref"], "sha256": ref["sha256"]})
    contract = {"schema_version": "run-contract.v1", "workflow": {"kind": "prd"}}
    pinned = write_run_contract_snapshot(state, {**contract, "contract_digest": stable_json_sha256(contract)})
    package = build_plan_artifact_package(workflow_run_id="run", flow_kind="prd",
        producer_stage_id="plan", run_contract=pinned, plan_revision="r1", task_map_generation="g1",
        produced=ports, required_ports=GLOBAL)
    descriptor = write_plan_artifact_package(state, package)
    return {"task_map_generation": "g1", "plan_artifact_package_ref": descriptor["ref"],
            "plan_artifact_package_digest": descriptor["sha256"]}


@pytest.mark.parametrize("selection", [False, True])
def test_ingested_contract_delivers_local_ports_through_real_reader(tmp_path, selection):
    body = _map(global_ports=GLOBAL, local_ports=["local_detail", "local_extra"], selection=selection)
    result = ingest_task_map_to_kanban(tmp_path, body)
    assert result.passed, result.errors
    identity = _bind_package(tmp_path, body)
    for profile in ("implementation", "task-verify"):
        sources = current_plan_port_sources(state_dir=tmp_path, workflow_run_id="run",
            task_id=TASK, currentness=identity, profile=profile)
        expected = {"task_execution_input", "local_detail"} if selection else {*GLOBAL, "local_detail", "local_extra"}
        assert {source["artifact_id"] for source in sources} == expected
        local = next(source for source in sources if source["artifact_id"] == "local_detail")
        assert local["allowed_paths"] == ["$.shared", "$.tasks[0]"]
    task = TaskStore(tmp_path / "kanban.json").get(TASK)
    assert task.contract.evidence_contract.get("plan_input_selection") == body["tasks"][0]["evidence_contract"].get("plan_input_selection")


@pytest.mark.parametrize("selection", [False, True])
def test_missing_task_local_port_fails_before_read_selection(tmp_path, selection):
    body = _map(global_ports=GLOBAL, local_ports=["local_detail", "local_extra"], selection=selection)
    assert ingest_task_map_to_kanban(tmp_path, body).passed
    identity = _bind_package(tmp_path, body, missing=["local_extra"])
    with pytest.raises(ArtifactReadError, match="does not bind task required ports: local_extra"):
        current_plan_port_sources(state_dir=tmp_path, workflow_run_id="run",
            task_id=TASK, currentness=identity, profile="implementation")


@pytest.mark.parametrize("local", [["../bad"], ["local_detail", "local_detail"], ["requirement_spec", "prd_ref"]])
def test_existing_ingest_validation_rejects_invalid_local_declarations(tmp_path, local):
    result = ingest_task_map_to_kanban(tmp_path, _map(global_ports=GLOBAL, local_ports=local))
    assert not result.passed
    assert any("required_plan_ports" in error for error in result.errors)
    assert not TaskStore(tmp_path / "kanban.json").list_all()


def test_ingest_does_not_grant_unbound_selection(tmp_path):
    body = _map(global_ports=GLOBAL, local_ports=["local_detail"], selection=True)
    body["tasks"][0]["evidence_contract"]["plan_input_selection"]["implementation"].append("unknown")
    result = ingest_task_map_to_kanban(tmp_path, body)
    assert not result.passed
    assert any("unbound" in error for error in result.errors)
    assert not TaskStore(tmp_path / "kanban.json").list_all()
