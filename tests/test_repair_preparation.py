"""doc 78 O-2 (safe half): repair PREPARATION never auto-applies."""

from __future__ import annotations

from zf.autoresearch.repair_preparation import (
    auto_prepare_enabled,
    build_repair_preparation,
    owner_message_for_prepared_repair,
    repair_prepared_payload,
)


def _bug_candidate_payload():
    return {
        "candidate": {"candidate_id": "AR-1", "hypothesis": "fix dispatch stall"},
        "candidate_path": "/state/artifacts/AR-1.json",
        "repair_task_payload": {
            "task_id": "TASK-AR-abc",
            "title": "Autoresearch self-repair: fix dispatch stall",
            "contract": {
                "scope": ["src/zf/**", "tests/**"],
                "verification": "Run focused autoresearch eval and relevant pytest target.",
            },
        },
    }


def test_opt_in_default_off():
    assert auto_prepare_enabled({}) is False
    assert auto_prepare_enabled({"ZF_AUTORESEARCH_AUTO_PREPARE": "true"}) is True
    assert auto_prepare_enabled({"ZF_AUTORESEARCH_AUTO_PREPARE": "0"}) is False


def test_preparation_carries_scope_and_task():
    prep = build_repair_preparation(_bug_candidate_payload())
    assert prep is not None
    assert prep.repair_task_id == "TASK-AR-abc"
    assert prep.scope == ("src/zf/**", "tests/**")
    assert prep.hypothesis == "fix dispatch stall"


def test_safety_invariant_auto_apply_always_false():
    # The whole point: the prepared record NEVER says auto-apply, and ALWAYS
    # requires human approval — these are codified constants, not configurable.
    prep = build_repair_preparation(_bug_candidate_payload())
    assert prep.auto_apply is False
    assert prep.requires_human_approval is True
    payload = repair_prepared_payload(prep)
    assert payload["auto_apply"] is False
    assert payload["requires_human_approval"] is True
    assert "manual" in payload["next_step"].lower()


def test_owner_message_asks_human_not_apply():
    prep = build_repair_preparation(_bug_candidate_payload())
    msg = owner_message_for_prepared_repair(prep)
    assert msg["route"] == "owner"
    assert "NOT auto-applied" in msg["text"]
    assert prep.repair_task_id in msg["text"]


def test_returns_none_without_repair_task():
    assert build_repair_preparation({"candidate": {}, "repair_task_payload": {}}) is None
    assert build_repair_preparation({}) is None
