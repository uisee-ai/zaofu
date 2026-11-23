"""Canonical replacements retain contracts and consume exact predecessor inputs."""
from copy import deepcopy

import pytest

from tests.test_task_plan_consumption import case  # noqa: F401
from tests.test_plan_candidate_preflight import _task_map
from zf.core.task.schema import Task, TaskContract
from zf.core.task.store import TaskStore
from zf.runtime.call_result_envelope import write_immutable_json_sidecar
from zf.runtime.module_gap_plan import build_gap_task_map_amend
from zf.runtime.plan_artifact_package import hydrate_plan_artifact_package, write_plan_artifact_package
from zf.runtime.sidecar_refs import hydrate_sidecar_ref
from zf.runtime.canonical_gap_rows import canonical_gap_rows


def test_canonical_replacement_reaches_scoped_input_consumer(case):
    state, resolver, identity, policy = case
    package = hydrate_plan_artifact_package(state, {"ref": identity["plan_artifact_package_ref"],
                                                   "sha256": identity["plan_artifact_package_digest"]})
    baseline = _task_map()
    row = baseline["tasks"][0]
    row.update(task_id="T1", source_ref="requirements.md", entry="standard",
               evidence_contract={"plan_input_selection": policy})
    row["validation"]["commands"][0]["producer_task_id"] = "T1"
    old_port = next(p for p in package["produced"] if p["logical_name"] == "task_map")
    old_ref = write_immutable_json_sidecar(state, baseline, root="fixtures/maps", kind="task_map",
        schema_version="task-map.v1", created_by="test")
    old_port.update(ref=old_ref["ref"], sha256=old_ref["sha256"])
    predecessor = write_plan_artifact_package(state, package)
    gap = {**deepcopy(row), "task_id": "FIX", "base_commit": "c" * 40, "supersedes_task_ids": ["T1"]}
    amended = build_gap_task_map_amend(baseline, gap_tasks=[gap], supersedes_task_map_ref=old_ref["ref"],
                                      successor_base_commit="c" * 40, preserve_task_rows=True)
    actual = amended["tasks"][0]
    assert actual["evidence_contract"] == row["evidence_contract"]
    assert actual["source_ref"] == row["source_ref"]
    assert actual["entry"] == row["entry"]
    assert actual["validation"]["commands"][0]["producer_task_id"] == "FIX"
    new_map = write_immutable_json_sidecar(state, amended, root="fixtures/maps", kind="task_map",
        schema_version="task-map.v1", created_by="test")
    old_port.update(ref=new_map["ref"], sha256=new_map["sha256"])
    inherited = [p for p in package["produced"] if p["logical_name"] in {"execution_spec", "task_execution_input"}]
    package["produced"] = [p for p in package["produced"] if p not in inherited]
    package["inherited"] = [{**p, "source_package_ref": predecessor["ref"],
        "source_package_digest": predecessor["sha256"]} for p in inherited]
    package.update(supersedes_package_ref=predecessor["ref"], supersedes_package_digest=predecessor["sha256"])
    pinned = write_plan_artifact_package(state, package)
    current = {**identity, "plan_artifact_package_ref": pinned["ref"], "plan_artifact_package_digest": pinned["sha256"]}
    TaskStore(state / "kanban.json").add(Task(id="FIX", contract=TaskContract(evidence_contract=actual["evidence_contract"])))
    for profile in ("implementation", "task-verify"):
        sources = resolver._current_plan_port_sources(workflow_run_id="run", task_id="FIX", currentness=current, profile=profile)
        assert [s["source_id"] for s in sources] == ["plan-port-task_execution_input-predecessor-T1"]
        assert sources[0]["allowed_paths"] == ["$.shared", "$.tasks[0]"]


@pytest.mark.parametrize("field", ["evidence_contract", "owner_role", "validation", "allowed_paths", "blocked_by"])
def test_canonical_mode_itself_rejects_contract_drift(field):
    baseline = _task_map()
    baseline["tasks"][0].update(owner_role="dev", source_ref="requirements.md", evidence_contract={"input": "bound"})
    gap = {**deepcopy(baseline["tasks"][0]), "task_id": "FIX", "base_commit": "c" * 40,
           "supersedes_task_ids": ["TASK-1"]}
    gap.pop(field)
    with pytest.raises(ValueError, match="changed its predecessor contract"):
        build_gap_task_map_amend(baseline, gap_tasks=[gap], supersedes_task_map_ref="bound-map",
                                successor_base_commit="c" * 40, preserve_task_rows=True)


def test_exact_obligations_comparison_allows_bound_dependency_identity_rebinding():
    baseline = _task_map()
    first = baseline["tasks"][0]
    first.update(source_ref="requirements.md", owner_role="dev")
    second = {**deepcopy(first), "task_id": "TASK-2", "dependencies": ["TASK-1"]}
    second.pop("blocked_by", None)
    second["allowed_paths"] = ["src/second.py", "tests/test_second.py"]
    second["claim_paths"] = list(second["allowed_paths"])
    baseline["tasks"].append(second)
    gaps = [{**deepcopy(row), "task_id": row["task_id"] + "-FIX", "base_commit": "c" * 40,
             "supersedes_task_ids": [row["task_id"]]} for row in baseline["tasks"]]
    amended = build_gap_task_map_amend(baseline, gap_tasks=gaps, supersedes_task_map_ref="bound-map",
                                      successor_base_commit="c" * 40, preserve_task_rows=True)
    actual = {row["task_id"]: row for row in amended["tasks"]}
    # Same exact-field comparison as the OA caller, after canonical mechanical rebinding.
    for expected in canonical_gap_rows(baseline, gaps, target_commit="c" * 40):
        for key, value in expected.items():
            if key not in {"supersedes_task_ids", "base_commit", "task_id", "wave", "source_refs"}:
                assert actual[expected["task_id"]].get(key) == value
    assert actual["TASK-2-FIX"]["blocked_by"] == ["TASK-1-FIX"]
