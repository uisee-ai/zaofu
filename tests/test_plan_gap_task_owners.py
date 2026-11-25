from copy import deepcopy
from dataclasses import replace

import pytest

from tests.test_plan_package_matrix_admission import case, _admit, _ready, _rejections
from zf.runtime.module_gap_plan import build_gap_task_map_amend
from zf.runtime.plan_artifact_package import hydrate_plan_artifact_package
from zf.runtime.sidecar_refs import hydrate_sidecar_ref
from zf.runtime.task_scoped_plan_inputs import task_input_paths


def _successor(case):
    original = case.body["tasks"][0]
    original["source_ref"] = "requirement.md"
    case.ports[1]["body"]["acceptance"][0]["statement"] = "Historical TASK-1 behavior"
    case.ports[2]["body"].setdefault("tests", []).append({
        "test_id": "recorded-test", "commands": ["test-command"], "task_id": "TASK-1",
    })
    for name in ("execution_spec", "task_execution_input"):
        case.ports.append({"logical_name": name, "artifact_kind": name,
                           "body": {"schema_version": "task-scoped-plan-input.v1", "shared": {"units": "bytes"},
                                    "tasks": [{"task_id": "TASK-1", "body": {"identity_example": "TASK-1"}}]}})
        case.body["required_plan_ports"].append(name)
    admitted = _admit(case, _ready(case))
    assert admitted is not None, _rejections(case)
    prior = hydrate_plan_artifact_package(case.state, {"ref": admitted.payload["plan_artifact_package_ref"],
                                                       "sha256": admitted.payload["plan_artifact_package_digest"]})
    ports = {p["logical_name"]: p for p in prior["produced"] + prior["inherited"]}
    parent_ref = ports["task_map"]["ref"]
    gap = {**deepcopy(original), "task_id": "TASK-REPAIR", "supersedes_task_ids": ["TASK-1"], "base_commit": "a"*40}
    new = build_gap_task_map_amend(case.body, gap_tasks=[gap], supersedes_task_map_ref=parent_ref,
                                   successor_base_commit="a"*40, preserve_task_rows=True)
    evt = _ready(case, body=new, ports=[], revision="repair")
    return replace(evt, payload={**evt.payload, "source_commit": "a"*40, "resume_scope": "gap_tasks_only",
        "task_ids": ["TASK-REPAIR"], "gap_task_ids": ["TASK-REPAIR"], "amend_of": parent_ref,
        "supersedes_task_map_generation": prior["task_map_generation"],
        "rework_summary": {"preserve_task_rows": True, "gap_tasks": [gap], "supersedes_task_ids": ["TASK-1"]}}), ports


def test_exact_gap_rebinds_current_owner_and_preserves_source_and_predecessor(case):
    evt, parents = _successor(case)
    admitted = _admit(case, evt)
    assert admitted is not None, _rejections(case)
    descriptor = {"ref": admitted.payload["plan_artifact_package_ref"], "sha256": admitted.payload["plan_artifact_package_digest"]}
    new = hydrate_plan_artifact_package(case.state, descriptor)
    ports = {p["logical_name"]: p for p in new["produced"] + new["inherited"]}
    for name, collection, key in [("acceptance_matrix", "acceptance", "task_id"), ("capability_matrix", "capabilities", "task_ids"),
                                  ("test_matrix", "tests", "task_id"), ("execution_spec", "tasks", "task_id"),
                                  ("task_execution_input", "tasks", "task_id")]:
        port = ports[name]
        old = hydrate_sidecar_ref(case.state, parents[name]).payload
        assert port["sha256"] != parents[name]["sha256"]
        body = hydrate_sidecar_ref(case.state, port).payload
        assert body[collection][-1][key] == (["TASK-REPAIR"] if key == "task_ids" else "TASK-REPAIR")
        assert old[collection][-1][key] == (["TASK-1"] if key == "task_ids" else "TASK-1")
        if name in {"execution_spec", "task_execution_input"}:
            assert task_input_paths(body, task_id="TASK-REPAIR") == ["$.shared", "$.tasks[0]"]
            assert body["tasks"][0]["body"]["identity_example"] == "TASK-1"
    assert hydrate_sidecar_ref(case.state, ports["acceptance_matrix"]).payload["acceptance"][0]["statement"] == "Historical TASK-1 behavior"
    assert _admit(case, evt).payload["plan_artifact_package_digest"] == descriptor["sha256"]


@pytest.mark.parametrize("fault", ["changed_contract", "changed_parent"])
def test_changed_contract_or_parent_cannot_inherit_owner_bindings(case, fault):
    evt, _ = _successor(case)
    if fault == "changed_contract":
        evt.payload["rework_summary"]["gap_tasks"][0]["acceptance_criteria"][0]["statement"] = "Changed."
    else:
        evt.payload["amend_of"] = "different-parent.json"
    assert _admit(case, evt) is None
    assert _rejections(case)
