"""Historical terminal rows must not be re-admitted under a new scheduler policy."""
from copy import deepcopy

import pytest

from tests.test_role_lifecycle_runtime import _runtime
from tests.test_task_pipeline_projection import _policy
from zf.core.events.model import ZfEvent
from zf.core.task.schema import Task
from zf.runtime.task_pipeline_projection import read_task_pipeline_projection
from zf.runtime.task_pipeline_runtime import prepare_task_pipeline_dispatch


def _seed(tmp_path, status):
    runtime, _, transport = _runtime(tmp_path)
    policy = _policy()
    for pool in policy["pools"].values():
        pool["role_instances"] = ["impl-2"]
    runtime.config.workflow.flow_metadata = {"task_pipeline": policy}
    runtime.config.workflow.orchestration.normal_frontier_owner = "wrc"
    runtime.task_store.add(Task(id="OLD", title="Historical task", status=status))
    generation = {
        "schema_version": "task-pipeline-generation.v1", "generation_id": "old-generation",
        "workflow_run_id": "run", "task_map_generation": "old-map",
        "flow_kind": "prd", "frontier_owner": "wrc", "task_ids": ["OLD"],
        "profile_id": policy["profile_id"], "profile_digest": "historical-policy-sha",
    }
    runtime.event_writer.append(ZfEvent(
        type="task.pipeline.generation.admitted", origin="kernel", payload=generation,
    ))
    return runtime, transport, generation


def _read(runtime):
    return read_task_pipeline_projection(
        runtime.state_dir, project_root=runtime.project_root, config=runtime.config,
    )


@pytest.mark.parametrize("status", ["done", "cancelled"])
def test_terminal_history_remains_visible_without_new_policy_admission(tmp_path, status):
    runtime, transport, _ = _seed(tmp_path, status)
    state_before = (runtime.state_dir / "kanban.json").read_bytes()
    events_before = (runtime.state_dir / "events.jsonl").read_bytes()
    projection = _read(runtime)
    assert projection["enabled"]
    assert projection["summary"]["task_statuses"] == {status: 1}
    assert projection["generations"][0]["task_map_generation"] == "old-map"
    assert not any(projection["dispatchable"].values())
    assert (runtime.state_dir / "events.jsonl").read_bytes() == events_before
    assert _read(runtime) == projection
    for _ in range(2):
        decisions, managed = prepare_task_pipeline_dispatch(runtime, candidate_task_ids=[])
        assert decisions == [] and managed == {"OLD"}
    assert not transport.sent and not transport.spawned
    assert (runtime.state_dir / "kanban.json").read_bytes() == state_before
    assert not [e for e in runtime.event_log.read_all() if e.type == "kernel.housekeeping.failed"]


def test_active_policy_mismatch_remains_fail_closed(tmp_path):
    runtime, transport, _ = _seed(tmp_path, "backlog")
    with pytest.raises(ValueError, match="admitted profile_digest"):
        _read(runtime)
    assert prepare_task_pipeline_dispatch(runtime, candidate_task_ids=["OLD"]) == ([], {"OLD"})
    assert not transport.sent
    assert any(e.type == "kernel.housekeeping.failed" for e in runtime.event_log.read_all())


@pytest.mark.parametrize("status,ready", [("done", True), ("cancelled", False)])
def test_new_generation_preserves_terminal_dependency_meaning(tmp_path, status, ready):
    runtime, _, generation = _seed(tmp_path, status)
    runtime.task_store.add(Task(id="NEW", title="New task", status="backlog", blocked_by=["OLD"]))
    current = deepcopy(generation)
    current.update(generation_id="new-generation", task_map_generation="new-map", task_ids=["NEW"],
                   profile_digest=runtime.config.workflow.flow_metadata["task_pipeline"]["profile_digest"])
    runtime.event_writer.append(ZfEvent(
        type="task.pipeline.generation.admitted", origin="kernel", payload=current,
    ))
    projection = _read(runtime)
    assert bool(projection["dispatchable"].get("impl")) is ready
    assert {t["task_id"]: t["task_status"] for t in projection["tasks"]} == {"OLD": status, "NEW": "backlog"}
    assert {g["task_map_generation"] for g in projection["generations"]} == {"old-map", "new-map"}
