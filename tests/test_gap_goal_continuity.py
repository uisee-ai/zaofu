"""Replacing tasks must not silently remove their explicit goal ownership."""

from copy import deepcopy
import json

import pytest

from tests.test_gap_command_catalog import _gap
from zf.core.events.model import ZfEvent
from zf.runtime.module_gap_plan import build_gap_task_map_amend


def amend(base, gaps):
    frozen = deepcopy((base, gaps))
    try:
        return build_gap_task_map_amend(
            base, gap_tasks=gaps, supersedes_task_map_ref="base.json"
        )
    finally:
        assert (base, gaps) == frozen


def test_real_bridge_rejects_removed_goal_mapping_before_publication(tmp_path):
    from tests.test_module_gap_plan_runtime import _state, _write_base_task_map

    state, log, transport, runtime = _state(tmp_path)
    ref = _write_base_task_map(state)
    path = tmp_path / ref
    base = json.loads(path.read_text())
    base["tasks"][0]["goal_claim_ids"] = ["GOAL-WEB"]
    path.write_text(json.dumps(base))
    gap = {**_gap(), "supersedes_task_ids": ["CANGJIE-WEB-001"]}
    event = ZfEvent(
        id="gap-ready",
        type="flow.gap_plan.ready",
        actor="zf-cli",
        payload={
            "pdd_id": "CANGJIE",
            "feature_id": "CANGJIE",
            "task_map_ref": ref,
            "gap_tasks": [gap],
        },
    )
    runtime.run_once(events=[event])
    runtime.run_once(events=[event])
    failures = [e for e in log.read_all() if e.type == "task_map.amend.failed"]
    assert len(failures) == 1
    assert "GOAL-WEB" in failures[0].payload["reason"]
    assert not any(e.type == "task_map.amended" for e in log.read_all())
    assert not list((state / "artifacts/CANGJIE/gap-amends").glob("*/task_map.json"))
    assert not transport.sent


@pytest.mark.parametrize("claims", [None, [], ["UNRELATED"]])
def test_missing_or_changed_goal_mapping_is_rejected(claims):
    base = {"tasks": [{"task_id": "OLD", "goal_claim_ids": ["GOAL-A"]}]}
    gap = {**_gap(), "supersedes_task_ids": ["OLD"]}
    if claims is not None:
        gap["goal_claim_ids"] = claims
    with pytest.raises(ValueError, match="GOAL-A"):
        amend(base, [gap])


def test_split_preserves_union_without_assigning_goals_to_every_successor():
    base = {"tasks": [{"task_id": "OLD", "goal_claim_ids": ["GOAL-A", "GOAL-B"]}]}
    a = {
        **_gap(),
        "task_id": "FIRST",
        "goal_claim_ids": ["GOAL-A"],
        "supersedes_task_ids": ["OLD"],
        "claim_paths": ["src/first/"],
    }
    b = {
        **_gap(),
        "task_id": "SECOND",
        "goal_claim_ids": ["GOAL-B"],
        "supersedes_task_ids": ["OLD"],
        "blocked_by": ["FIRST"],
        "claim_paths": ["src/second/"],
    }
    out = amend(base, [a, b])
    assert [t["goal_claim_ids"] for t in out["tasks"]] == [["GOAL-A"], ["GOAL-B"]]
    # Same-contract replay remains supported after the predecessor leaves the map.
    assert amend(out, [a, b])["tasks"] == out["tasks"]


def test_multiple_predecessors_cannot_lose_one_claim():
    base = {
        "tasks": [
            {"task_id": "A", "goal_claim_ids": ["GOAL-A"]},
            {"task_id": "B", "goal_claim_ids": ["GOAL-B"]},
        ]
    }
    gap = {**_gap(), "supersedes_task_ids": ["A", "B"], "goal_claim_ids": ["GOAL-A"]}
    with pytest.raises(ValueError, match="GOAL-B"):
        amend(base, [gap])
    gap["goal_claim_ids"].append("GOAL-B")
    assert amend(base, [gap])["tasks"][0]["goal_claim_ids"] == ["GOAL-A", "GOAL-B"]


def test_remaining_owner_and_legacy_unmapped_predecessor_stay_valid():
    base = {
        "tasks": [
            {"task_id": "OLD", "goal_claim_ids": ["GOAL-A"]},
            {"task_id": "RETAINED", "goal_claim_ids": ["GOAL-A"]},
        ]
    }
    gap = {**_gap(), "supersedes_task_ids": ["OLD"]}
    out = amend(base, [gap])
    assert "goal_claim_ids" not in out["tasks"][-1]
    assert out["tasks"][0]["goal_claim_ids"] == ["GOAL-A"]
    assert amend({"tasks": [{"task_id": "OLD"}]}, [gap])["tasks"]
