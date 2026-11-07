"""Repeated recovery keeps the exact inherited input and explicit ancestry."""
from copy import deepcopy

import pytest

from zf.core.task.schema import Task, TaskContract
from zf.core.task.store import TaskStore
from zf.runtime.artifact_read_ledger import ArtifactReadError
from zf.runtime.call_result_envelope import write_immutable_json_sidecar
from zf.runtime.plan_artifact_package import hydrate_plan_artifact_package, write_plan_artifact_package
from zf.runtime.sidecar_refs import hydrate_sidecar_ref
from tests.test_replacement_plan_inputs import _replacement
from tests.test_task_plan_consumption import case  # noqa: F401


def _next(state, identity, predecessor, successor, *, fault=""):
    prior = {"ref": identity["plan_artifact_package_ref"], "sha256": identity["plan_artifact_package_digest"]}
    package = deepcopy(hydrate_plan_artifact_package(state, prior))
    original_inputs = deepcopy(package["inherited"])
    if fault:
        if fault == "run":
            package["workflow_run_id"] = "other"
        else:
            port = next(p for p in package["produced"] if p["logical_name"] == "task_map")
            body = deepcopy(hydrate_sidecar_ref(state, port).payload)
            if fault == "ambiguous":
                body["tasks"][0]["payload"]["supersedes_task_ids"] = ["T1", "T2"]
            elif fault == "non_gap":
                body["amend"]["kind"] = "other"
            elif fault == "unrelated":
                body["tasks"][0]["task_id"] = "UNRELATED"
            elif fault == "input_changed":
                p = next(p for p in package["inherited"] if p["logical_name"] == "task_execution_input")
                replacement = write_immutable_json_sidecar(state, {"changed": True}, root="fixtures",
                    kind="task_execution_input", schema_version="v1", created_by="test")
                p.update(ref=replacement["ref"], sha256=replacement["sha256"])
            ref = write_immutable_json_sidecar(state, body, root="fixtures", kind="task_map",
                                              schema_version="v1", created_by="test")
            port.update(ref=ref["ref"], sha256=ref["sha256"])
        prior = write_plan_artifact_package(state, package)
        if fault == "input_changed":
            package["inherited"] = original_inputs
        # The current package remains in the original Run.
        package["workflow_run_id"] = "run"
    task_map = {"tasks": [{"task_id": successor, "payload": {"supersedes_task_ids": [predecessor]},
                            "evidence_contract": {"supersedes_task_ids": [predecessor]}}],
                "amend": {"kind": "goal_gap", "gap_task_ids": [successor],
                          "superseded_task_ids": [predecessor]}}
    ref = write_immutable_json_sidecar(state, task_map, root="fixtures", kind="task_map",
                                      schema_version="v1", created_by="test")
    for port in package["produced"]:
        if port["logical_name"] == "task_map":
            port.update(ref=ref["ref"], sha256=ref["sha256"])
    for port in package["inherited"]:
        port.update(source_package_ref=prior["ref"], source_package_digest=prior["sha256"])
    package.update(supersedes_package_ref=prior["ref"], supersedes_package_digest=prior["sha256"])
    descriptor = write_plan_artifact_package(state, package)
    store = TaskStore(state / "kanban.json")
    if store.get(successor) is None:
        store.add(Task(id=successor, status="in_progress", contract=TaskContract()))
    return {**identity, "plan_artifact_package_ref": descriptor["ref"],
            "plan_artifact_package_digest": descriptor["sha256"]}


@pytest.mark.parametrize("profile", ["implementation", "task-verify"])
@pytest.mark.parametrize("depth", [2, 3])
def test_repeated_replacement_selects_original_partition(case, profile, depth):
    state, resolver, identity = _replacement(case)
    task = "NEW"
    for i in range(1, depth):
        successor = f"NEW-{i}"
        identity = _next(state, identity, task, successor)
        task = successor
    sources = resolver._current_plan_port_sources(workflow_run_id="run", task_id=task,
                                                 currentness=identity, profile=profile)
    inherited = [s for s in sources if "-predecessor-" in s["source_id"]]
    assert {s["source_id"] for s in inherited} == {
        "plan-port-execution_spec-predecessor-T1", "plan-port-task_execution_input-predecessor-T1"}
    assert all(s["allowed_paths"] == ["$.shared", "$.tasks[0]"] for s in inherited)
    original = hydrate_plan_artifact_package(state, {"ref": case[2]["plan_artifact_package_ref"],
                                                     "sha256": case[2]["plan_artifact_package_digest"]})
    for source in inherited:
        port = next(p for p in original["produced"] if p["logical_name"] == source["artifact_id"])
        assert (source["ref"], source["sha256"]) == (port["ref"], port["sha256"])


@pytest.mark.parametrize("fault", ["run", "ambiguous", "non_gap", "unrelated", "input_changed"])
def test_repeated_replacement_rejects_broken_ancestry(case, fault):
    state, resolver, identity = _replacement(case)
    identity = _next(state, identity, "NEW", "NEW-2", fault=fault)
    with pytest.raises(ArtifactReadError):
        resolver._current_plan_port_sources(workflow_run_id="run", task_id="NEW-2",
                                             currentness=identity, profile="implementation")


def test_replacement_cycle_is_rejected(case):
    state, resolver, identity = _replacement(case)
    identity = _next(state, identity, "NEW", "NEXT")
    identity = _next(state, identity, "NEXT", "NEW")
    with pytest.raises(ArtifactReadError, match="cycle"):
        resolver._current_plan_port_sources(workflow_run_id="run", task_id="NEW",
                                             currentness=identity, profile="implementation")
