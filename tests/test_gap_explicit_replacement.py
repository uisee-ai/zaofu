"""Explicit producer lineage survives real controlled gap adoption."""
import copy
import json

import pytest

from zf.runtime.module_gap_plan import gap_tasks_from_rework_summary
from tests.test_control_actions_recovery import _service


def _gaps():
    return [{"task_id": tid, "parent_task_id": "OLD", "supersedes_task_ids": supersedes,
             "blocked_by": dependencies, "claim_paths": [f"src/{tid}/**", f"tests/{tid}"],
             "acceptance": [f"{tid} works"], "verify_commands": [f"pytest tests/{tid}"],
             "base_commit": "a" * 40, "source_refs": ["event:gap", "git:" + "a" * 40]}
            for tid, supersedes, dependencies in [("PORT", [], []), ("NEW", ["OLD"], ["PORT"])]]


def test_explicit_empty_supersedes_preserves_producer_despite_matching_parent():
    summary = {"supersedes_task_ids": ["OLD"], "gap_tasks": _gaps()}
    before = copy.deepcopy(summary)
    tasks = gap_tasks_from_rework_summary(summary)
    assert tasks[0]["supersedes_task_ids"] == []
    assert tasks[1]["supersedes_task_ids"] == ["OLD"]
    assert summary == before


@pytest.mark.parametrize("producer_declares_empty", [False, True])
def test_controlled_retrigger_rewires_only_to_declared_terminal_successor(tmp_path, producer_declares_empty):
    svc = _service(tmp_path)
    base = {"schema_version": "task-map.v1", "tasks": [
        {"task_id": tid, "title": tid, "owner_role": "dev", "wave": i,
         "blocked_by": dependencies, "allowed_paths": [f"src/{tid}/**"],
         "allowed_paths_reason": "owned module", "acceptance": [f"{tid} works"]}
        for i, (tid, dependencies) in enumerate([("OLD", []), ("DOWNSTREAM", ["OLD"])])]}
    (svc.state_dir / "base.json").write_text(json.dumps(base))
    source = svc.writer.emit("flow.discovery.failed", actor="test", payload={})
    request = svc.writer.emit("control.action.requested", actor="operator", payload={})
    gaps = _gaps()
    if not producer_declares_empty:
        gaps[0].pop("supersedes_task_ids")
    result = svc.execute(action="candidate-rework-apply", requested_action="candidate-rework-apply",
        requested=request, payload={"pdd_id": "P1", "source_event_id": source.id,
          "candidate_rework_action": "retrigger", "checkpoint_id": "explicit-lineage",
          "task_map_ref": "base.json", "source_commit": "a" * 40, "candidate_base_commit": "a" * 40,
          "rework_summary": {"supersedes_task_ids": ["OLD"], "gap_tasks": gaps}})
    assert result["ok"], json.dumps(result)
    ready = next(e for e in svc.writer.event_log.read_all() if e.type == "task_map.ready")
    body = json.loads((svc.state_dir / ready.payload["task_map_ref"]).read_text())
    tasks = {t["task_id"]: t for t in body["tasks"]}
    assert set(tasks) == {"PORT", "NEW", "DOWNSTREAM"}
    assert tasks["PORT"]["payload"]["supersedes_task_ids"] == []
    assert not tasks["PORT"]["evidence_contract"].get("supersedes_task_ids")
    assert tasks["NEW"]["payload"]["supersedes_task_ids"] == ["OLD"]
    assert tasks["NEW"]["blocked_by"] == ["PORT"]
    assert tasks["DOWNSTREAM"]["blocked_by"] == ["NEW"]
