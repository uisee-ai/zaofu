"""Unrelated partition updates cannot freeze an unchanged recovery task."""
from copy import deepcopy

import pytest

from tests.test_task_plan_consumption import case  # noqa: F401
from tests.test_replacement_plan_inputs import _replacement
from tests.test_replacement_input_lineage import _next
from zf.runtime.artifact_read_ledger import ArtifactReadError
from zf.runtime.call_result_envelope import write_immutable_json_sidecar
from zf.runtime.plan_artifact_package import hydrate_plan_artifact_package, write_plan_artifact_package
from zf.runtime.plan_candidate_input_lineage import validate_dispatch_input_partitions
from zf.runtime.sidecar_refs import hydrate_sidecar_ref


def _revise(state, identity, fault=""):
    prior = {"ref": identity["plan_artifact_package_ref"], "sha256": identity["plan_artifact_package_digest"]}
    package = deepcopy(hydrate_plan_artifact_package(state, prior))
    task_port = next(p for p in package["produced"] if p["logical_name"] == "task_map")
    task_map = deepcopy(hydrate_sidecar_ref(state, task_port).payload)
    task_map["amend"] = {"kind": "goal_gap", "gap_task_ids": ["OTHER"], "superseded_task_ids": ["OLD-OTHER"]}
    ref = write_immutable_json_sidecar(state, task_map, root="fixtures", kind="task_map", schema_version="v1", created_by="test")
    task_port.update(ref=ref["ref"], sha256=ref["sha256"])
    for port in list(package["inherited"]):
        if port["logical_name"] not in {"task_execution_input", "execution_spec"}:
            continue
        body = deepcopy(hydrate_sidecar_ref(state, port).payload)
        body["tasks"].append({"task_id": "UNRELATED", "body": {"new": True}})
        body["tasks"].reverse()
        if fault == "shared": body["shared"]["rule"] = "changed"
        elif fault == "owned": body["tasks"][-1]["body"]["new"] = True
        elif fault == "missing": body["tasks"] = body["tasks"][:-1]
        elif fault == "duplicate": body["tasks"].append(deepcopy(body["tasks"][-1]))
        descriptor = write_immutable_json_sidecar(state, body, root="fixtures", kind=port["logical_name"],
                                                 schema_version=port["schema_version"], created_by="test")
        package["inherited"].remove(port)
        package["produced"].append({**port, "ref": descriptor["ref"], "sha256": descriptor["sha256"]})
    package.update(supersedes_package_ref=prior["ref"], supersedes_package_digest=prior["sha256"])
    descriptor = write_plan_artifact_package(state, package)
    return {**identity, "plan_artifact_package_ref": descriptor["ref"],
            "plan_artifact_package_digest": descriptor["sha256"]}


@pytest.mark.parametrize("profile", ["implementation", "task-verify"])
def test_worker_reads_unchanged_predecessor_after_unrelated_partition_revision(case, profile):
    state, resolver, identity = _replacement(case)
    identity = _next(state, _revise(state, identity), "NEW", "NEXT")
    sources = resolver._current_plan_port_sources(workflow_run_id="run", task_id="NEXT",
                                                 currentness=identity, profile=profile)
    inherited = [s for s in sources if "-predecessor-T1" in s["source_id"]]
    assert len(inherited) == 2
    for source in inherited:
        body = hydrate_sidecar_ref(state, source).payload
        assert source["allowed_paths"] == ["$.shared", f"$.tasks[{len(body['tasks']) - 1}]"]
        assert body["tasks"][-1]["task_id"] == "T1"


def test_candidate_preflight_follows_same_unchanged_partition(case):
    state, _, identity = _replacement(case)
    identity = _revise(state, identity)
    package = hydrate_plan_artifact_package(state, {"ref": identity["plan_artifact_package_ref"],
                                                   "sha256": identity["plan_artifact_package_digest"]})
    task_map = {"tasks": [{"task_id": "NEXT", "payload": {"supersedes_task_ids": ["NEW"]},
                          "evidence_contract": {"supersedes_task_ids": ["NEW"]}}],
                "amend": {"kind": "goal_gap", "gap_task_ids": ["NEXT"], "superseded_task_ids": ["NEW"]}}
    validate_dispatch_input_partitions(state_dir=state, task_map=task_map,
        ports=package["produced"] + package["inherited"], required_ports=["task_execution_input"],
        trigger={**identity, "workflow_run_id": "run"}, task_ids=["NEXT"])


@pytest.mark.parametrize("fault", ["shared", "owned", "missing", "duplicate"])
def test_worker_rejects_changed_or_unprovable_partition(case, fault):
    state, resolver, identity = _replacement(case)
    identity = _next(state, _revise(state, identity, fault), "NEW", "NEXT")
    with pytest.raises(ArtifactReadError):
        resolver._current_plan_port_sources(workflow_run_id="run", task_id="NEXT",
                                             currentness=identity, profile="implementation")


def test_changed_then_restored_shared_input_is_not_continuous(case):
    state, resolver, identity = _replacement(case)
    changed = _revise(state, identity, "shared")
    package = deepcopy(hydrate_plan_artifact_package(state, {
        "ref": changed["plan_artifact_package_ref"], "sha256": changed["plan_artifact_package_digest"]}))
    original = hydrate_plan_artifact_package(state, {
        "ref": identity["plan_artifact_package_ref"], "sha256": identity["plan_artifact_package_digest"]})
    for port in package["produced"]:
        prior = next((p for p in original["inherited"] if p["logical_name"] == port["logical_name"]), None)
        if prior:
            port.update(ref=prior["ref"], sha256=prior["sha256"])
    package.update(supersedes_package_ref=changed["plan_artifact_package_ref"],
                   supersedes_package_digest=changed["plan_artifact_package_digest"])
    ref = write_plan_artifact_package(state, package)
    identity = _next(state, {**identity, "plan_artifact_package_ref": ref["ref"],
        "plan_artifact_package_digest": ref["sha256"]}, "NEW", "NEXT")
    with pytest.raises(ArtifactReadError):
        resolver._current_plan_port_sources(workflow_run_id="run", task_id="NEXT",
                                             currentness=identity, profile="implementation")


def test_scope_comparison_does_not_equate_json_boolean_and_number():
    from zf.runtime.task_scoped_plan_inputs import unchanged_task_input
    body = {"schema_version": "task-scoped-plan-input.v1", "shared": {},
            "tasks": [{"task_id": "T", "body": {"enabled": True}}]}
    changed = deepcopy(body)
    changed["tasks"][0]["body"]["enabled"] = 1
    assert not unchanged_task_input(body, changed, task_id="T")
