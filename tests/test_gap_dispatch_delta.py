"""A new gap amendment dispatches its own delta, never a prior plan's batch."""
from copy import deepcopy
import json

import pytest

from zf.runtime.module_gap_plan import build_gap_task_map_amend
from zf.runtime.writer_fanout_admission import select_writer_task_items, writer_task_items


def _base():
    return {"schema_version": "task-map.v1", "resume_scope": "gap_tasks_only", "tasks": [
        {"task_id": tid, "title": tid, "owner_role": "dev", "wave": 0,
         "allowed_paths": [f"src/{tid}/**"], "allowed_paths_reason": "owned slice",
         "acceptance": ["works"]} for tid in ["DONE", "OLD"]]}


def _seed_delta(base, location):
    if location in {"top", "both"}:
        base["dispatch_delta_task_ids"] = ["DONE", "OLD"]
    if location in {"metadata", "both"}:
        base["metadata"] = {"dispatch_delta_task_ids": ["DONE", "OLD"],
                            "history_ref": "prior-plan.json"}


def _gap():
    return {"task_id": "NEW", "parent_task_id": "OLD", "supersedes_task_ids": ["OLD"],
            "owner_role": "dev", "claim_paths": ["src/OLD/**"], "acceptance": ["works"],
            "verify_commands": ["pytest tests/test_old.py"], "base_commit": "a" * 40,
            "source_refs": ["event:failure", "git:" + "a" * 40]}


@pytest.mark.parametrize("location", ["top", "metadata", "both"])
def test_replacement_rebinds_prior_delta_without_mutating_baseline(location):
    base = _base()
    _seed_delta(base, location)
    before = deepcopy(base)
    amended = build_gap_task_map_amend(base, gap_tasks=[_gap()], supersedes_task_map_ref="old.json")
    selected, requested, _, scope = select_writer_task_items(
        amended, writer_task_items(amended), {"task_ids": ["NEW"], "resume_scope": "gap_tasks_only"})
    assert [task["task_id"] for task in selected] == requested == ["NEW"]
    assert scope == "gap_tasks_only"
    assert base == before
    assert {task["task_id"] for task in amended["tasks"]} == {"DONE", "NEW"}
    if location in {"top", "both"}:
        assert amended["dispatch_delta_task_ids"] == ["NEW"]
    if location in {"metadata", "both"}:
        assert amended["metadata"] == {"dispatch_delta_task_ids": ["NEW"], "history_ref": "prior-plan.json"}
    with pytest.raises(RuntimeError, match="dispatch_delta_task_ids disagree"):
        select_writer_task_items(amended, writer_task_items(amended),
                                 {"task_ids": ["DONE"], "resume_scope": "gap_tasks_only"})


@pytest.mark.parametrize("location", ["top", "metadata", "both"])
def test_real_gap_event_dispatches_only_new_task_and_deduplicates_restart(tmp_path, monkeypatch, location):
    from tests import test_module_gap_plan_runtime as runtime

    original = runtime._write_base_task_map

    def write_prior_batch(state):
        ref = original(state)
        # Resolve the actual fixture ref, including its configured state prefix.
        path = state / ref.removeprefix(".zf/")
        body = json.loads(path.read_text())
        _seed_delta(body, location)
        body["resume_scope"] = "gap_tasks_only"
        path.write_text(json.dumps(body))
        return ref

    monkeypatch.setattr(runtime, "_write_base_task_map", write_prior_batch)
    runtime.test_flow_neutral_gap_plan_ready_amends_task_map_and_dispatches_gap_task(tmp_path)
