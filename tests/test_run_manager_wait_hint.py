from __future__ import annotations

from zf.runtime.run_manager_wait_hint import (
    build_resident_repair_policy_projection,
    build_wait_hint_projection,
)


def test_wait_hints_are_non_blocking() -> None:
    hints = build_wait_hint_projection(
        monitor={"state": "healthy_waiting", "in_flight_tasks": []},
        completion_profile={"pending_human_decisions": [{"decision_token": "hdec"}]},
        repair_merge_queue={"summary": {"pending": 1}},
        no_progress={"status": "tripped"},
    )

    assert hints["blocking"] is False
    assert {item["kind"] for item in hints["items"]} >= {
        "idle_no_inflight",
        "repair_closeout_pending",
        "human_decision_pending",
        "no_progress_tripped",
    }


def test_resident_repair_policy_allows_bounded_repair_by_default() -> None:
    policy = build_resident_repair_policy_projection()

    assert policy["enabled"] is True
    assert policy["execution"]["mode"] == "bounded_repair_worker"
    assert policy["execution"]["requires_closeout_gate"] is True
    assert policy["resident"]["can_request_repair"] is True
    assert policy["resident"]["auto_merge"] is False
    assert policy["resident"]["requires_explicit_config"] is False
