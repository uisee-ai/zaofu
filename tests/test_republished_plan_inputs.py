"""A replan may explicitly republish an unchanged, provenance-bound input."""
from copy import deepcopy

import pytest

from zf.runtime.artifact_read_ledger import ArtifactReadError
from zf.runtime.call_result_envelope import write_immutable_json_sidecar
from zf.runtime.plan_artifact_package import hydrate_plan_artifact_package, write_plan_artifact_package
from tests.test_replacement_plan_inputs import _replacement
from tests.test_replacement_input_lineage import _next
from tests.test_task_plan_consumption import case  # noqa: F401


def _republish(state, identity, fault=""):
    package = deepcopy(hydrate_plan_artifact_package(state, {
        "ref": identity["plan_artifact_package_ref"], "sha256": identity["plan_artifact_package_digest"]}))
    for port in package["inherited"]:
        port.pop("source_package_ref", None)
        port.pop("source_package_digest", None)
        package["produced"].append(port)
    package["inherited"] = []
    if fault in {"no_parent", "forged_source"}:
        if fault == "forged_source":
            for port in package["produced"]:
                if port["logical_name"] in {"execution_spec", "task_execution_input"}:
                    port.update(source_package_ref=package["supersedes_package_ref"],
                                source_package_digest=package["supersedes_package_digest"])
        package.pop("supersedes_package_ref", None)
        package.pop("supersedes_package_digest", None)
    elif fault in {"run", "contract"}:
        parent = deepcopy(hydrate_plan_artifact_package(state, {
            "ref": package["supersedes_package_ref"], "sha256": package["supersedes_package_digest"]}))
        parent["workflow_run_id" if fault == "run" else "run_contract_digest"] = "other" if fault == "run" else "f" * 64
        ref = write_plan_artifact_package(state, parent)
        package.update(supersedes_package_ref=ref["ref"], supersedes_package_digest=ref["sha256"])
    elif fault == "schema_drift":
        port = next(p for p in package["produced"] if p["logical_name"] == "task_execution_input")
        port["schema_version"] = "unreviewed-schema.v9"
    elif fault in {"changed_input", "same_body_new_ref"}:
        port = next(p for p in package["produced"] if p["logical_name"] == "task_execution_input")
        from zf.runtime.sidecar_refs import hydrate_sidecar_ref
        body = deepcopy(hydrate_sidecar_ref(state, port).payload)
        if fault == "changed_input":
            body["shared"]["new_rule"] = "must not lose this"
        ref = write_immutable_json_sidecar(state, body, root="republished", kind="task_execution_input", schema_version="v1", created_by="test")
        port.update(ref=ref["ref"], sha256=ref["sha256"])
    ref = write_plan_artifact_package(state, package)
    return {**identity, "plan_artifact_package_ref": ref["ref"], "plan_artifact_package_digest": ref["sha256"]}


@pytest.mark.parametrize("profile", ["implementation", "task-verify"])
@pytest.mark.parametrize("depth", [1, 2])
def test_explicit_republication_retains_proven_original_partition(case, profile, depth):
    state, resolver, identity = _replacement(case)
    identity = _republish(state, identity)
    task = "NEW"
    if depth == 2:
        # Bridge through an intermediate package whose input is also produced.
        identity = _next(state, identity, task, "NEW-2")
        task = "NEW-2"
    before = (state / identity["plan_artifact_package_ref"]).read_bytes()
    sources = resolver._current_plan_port_sources(workflow_run_id="run", task_id=task, currentness=identity, profile=profile)
    inputs = [p for p in sources if p["artifact_id"] in {"task_execution_input", "execution_spec"}]
    assert len(inputs) == 2
    assert all(p["allowed_paths"] == ["$.shared", "$.tasks[0]"] for p in inputs)
    assert all(p["source_id"].endswith("-predecessor-T1") for p in inputs)
    assert (state / identity["plan_artifact_package_ref"]).read_bytes() == before


@pytest.mark.parametrize("fault", ["no_parent", "forged_source", "schema_drift", "run", "contract", "changed_input", "same_body_new_ref"])
def test_republication_without_exact_ancestry_fails_closed(case, fault):
    state, resolver, identity = _replacement(case)
    identity = _republish(state, identity, fault)
    with pytest.raises(ArtifactReadError):
        resolver._current_plan_port_sources(workflow_run_id="run", task_id="NEW", currentness=identity, profile="implementation")
