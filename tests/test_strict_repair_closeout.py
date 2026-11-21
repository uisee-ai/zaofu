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


def test_medium_risk_projection_does_not_invent_owner_gate(tmp_path):
    from zf.runtime.self_repair_runner import _classify_closeout_risk

    risk = _classify_closeout_risk(["src/zf/runtime/example.py"], commits_ahead=1)
    assert risk["risk"] == "medium"
    assert risk["human_approval_required"] is None
    _, log, writer = _state(tmp_path)
    writer.emit("autoresearch.repair.closeout.required", payload={
        "queue_id": "candidate-medium", "risk_classification": {
            "risk": "medium", "controlled_apply_allowed": False, "human_approval_required": True,
        }})
    row = build_repair_merge_queue(log.read_all())["items"][0]
    assert row["apply_candidate"]["decision"] == "policy_evaluation_required"
    assert row["apply_candidate"]["auto_merge"] is False
