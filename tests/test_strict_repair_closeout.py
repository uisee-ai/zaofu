"""A strict repair candidate needs disposition even when unrelated work progresses."""
import pytest

from tests.test_run_manager import _state
from zf.runtime.run_manager import build_repair_merge_queue, build_run_completion_profile


@pytest.mark.parametrize("progress", ["run.manager.human_decision.applied", "task_map.ready", "run.completed"])
@pytest.mark.parametrize("strict", [False, True])
def test_progress_does_not_dispose_strict_repair(tmp_path, progress, strict):
    _, log, writer = _state(tmp_path)
    writer.default_origin = "kernel"
    writer.emit("autoresearch.repair.closeout.required", payload={
        "queue_id": "candidate-one", "repair_contract_required": strict,
        "repair_contract_digest": "frozen-contract", "source_commit": "repair-commit",
        "continuation": {"resume_original_workflow": True},
    })
    writer.emit(progress, payload={"status": "passed"})
    profile = build_run_completion_profile(log.read_all(), goal={"status": "complete"},
        repair_ledger={}, repair_merge_queue=build_repair_merge_queue(log.read_all()))
    assert ("repair_closeout_pending" in profile["blockers"]) is strict
    assert profile["status"] == ("blocked" if strict else "complete")
