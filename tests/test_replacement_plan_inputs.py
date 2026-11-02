"""Replacement tasks read only explicit, pinned predecessor inputs."""
from copy import deepcopy

import pytest

from zf.core.task.schema import Task, TaskContract
from zf.core.task.store import TaskStore
from zf.runtime.artifact_read_ledger import ArtifactReadError, render_attempt_source_briefing
from zf.runtime.call_result_envelope import write_immutable_json_sidecar
from zf.runtime.plan_artifact_package import hydrate_plan_artifact_package, write_plan_artifact_package
from tests.test_task_plan_consumption import case  # noqa: F401


def _replacement(case, *, predecessor="T1", inherited=True, gap=True):
    state, resolver, identity, _ = case
    previous_ref = identity["plan_artifact_package_ref"]
    previous_digest = identity["plan_artifact_package_digest"]
    package = hydrate_plan_artifact_package(state, {"ref": previous_ref, "sha256": previous_digest})
    task_map = {"tasks": [{"task_id": "NEW", "payload": {"supersedes_task_ids": [predecessor]},
                           "evidence_contract": {"supersedes_task_ids": [predecessor]}}],
                "amend": {"kind": "goal_gap" if gap else "other", "gap_task_ids": ["NEW"],
                          "superseded_task_ids": [predecessor]}}
    new_map = write_immutable_json_sidecar(state, task_map, root="fixtures", kind="task_map",
                                          schema_version="v1", created_by="test")
    for port in package["produced"]:
        if port["logical_name"] == "task_map":
            port.update(ref=new_map["ref"], sha256=new_map["sha256"])
    if inherited:
        names = {"execution_spec", "task_execution_input"}
        old_ports = [p for p in package["produced"] if p["logical_name"] in names]
        package["produced"] = [p for p in package["produced"] if p["logical_name"] not in names]
        package["inherited"] = [{**p, "source_package_ref": previous_ref,
                                  "source_package_digest": previous_digest} for p in old_ports]
    package["supersedes_package_ref"] = previous_ref
    package["supersedes_package_digest"] = previous_digest
    descriptor = write_plan_artifact_package(state, package)
    identity = {**identity, "plan_artifact_package_ref": descriptor["ref"],
                "plan_artifact_package_digest": descriptor["sha256"]}
    TaskStore(state / "kanban.json").add(Task(id="NEW", status="in_progress", contract=TaskContract()))
    return state, resolver, identity


def test_inherited_partition_reads_parent_as_labeled_context(case):
    state, resolver, identity = _replacement(case)
    for profile in ("implementation", "task-verify"):
        sources = resolver._current_plan_port_sources(workflow_run_id="run", task_id="NEW",
                                                       currentness=identity, profile=profile)
        inherited = [source for source in sources if "-predecessor-" in source["source_id"]]
        assert {source["source_id"] for source in inherited} == {
            "plan-port-execution_spec-predecessor-T1", "plan-port-task_execution_input-predecessor-T1"}
        assert all(source["allowed_paths"] == ["$.shared", "$.tasks[0]"] for source in inherited)
    briefing = render_attempt_source_briefing({"attempt_source_manifest_ref": "manifest", "attempt_id": "a1",
        "required_reads": [{"source_id": "plan-port-execution_spec-predecessor-T1", "artifact_id": "execution_spec",
                            "json_path": "$.tasks[0]"}]})
    assert "historical predecessor context" in briefing
    assert "current task contract" in briefing


@pytest.mark.parametrize("fault", ["not_inherited", "not_gap", "not_in_ancestry"])
def test_replacement_rejects_unproven_predecessor(case, fault):
    state, resolver, identity = _replacement(case, predecessor="T2" if fault == "not_in_ancestry" else "T1",
                                             inherited=fault != "not_inherited", gap=fault != "not_gap")
    with pytest.raises(ArtifactReadError):
        resolver._current_plan_port_sources(workflow_run_id="run", task_id="NEW",
                                             currentness=identity, profile="implementation")


def test_replacement_rejects_ambiguous_predecessors(case):
    state, resolver, identity = _replacement(case)
    package = hydrate_plan_artifact_package(state, {"ref": identity["plan_artifact_package_ref"],
                                                   "sha256": identity["plan_artifact_package_digest"]})
    from zf.runtime.sidecar_refs import hydrate_sidecar_ref
    port = next(p for p in package["produced"] if p["logical_name"] == "task_map")
    body = deepcopy(hydrate_sidecar_ref(state, port).payload)
    body["tasks"][0]["payload"]["supersedes_task_ids"].append("T2")
    ref = write_immutable_json_sidecar(state, body, root="fixtures", kind="task_map", schema_version="v1", created_by="test")
    port.update(ref=ref["ref"], sha256=ref["sha256"])
    descriptor = write_plan_artifact_package(state, package)
    identity.update(plan_artifact_package_ref=descriptor["ref"], plan_artifact_package_digest=descriptor["sha256"])
    with pytest.raises(ArtifactReadError, match="one explicit predecessor"):
        resolver._current_plan_port_sources(workflow_run_id="run", task_id="NEW", currentness=identity,
                                             profile="implementation")
