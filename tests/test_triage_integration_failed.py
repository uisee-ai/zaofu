"""doc 78 W2: classify integration.failed as plan-level vs impl-level.

Pre-fix: integration.failed had no branch in classify_rework_trigger, so it
fell through to 'ambiguous' -> orchestrator. A cherry-pick conflict (two slices
touching the same files = a decomposition/plan error) was therefore handled the
same as any ambiguous failure instead of routing to arch for re-plan. This was
the T1 死循环 surface in the cj-min refactor: a plan-level integration conflict
kept being re-implemented as if impl-level.

Fix: integration.failed with conflict / cherry-pick / overlap / scope signals
-> design_issue (arch, plan-level); otherwise -> product_issue (dev).
"""

from __future__ import annotations

from typing import Any

from zf.core.events.model import ZfEvent
from zf.runtime.rework_triage import classify_rework_trigger


def _ev(payload: dict[str, Any]) -> ZfEvent:
    return ZfEvent(type="integration.failed", actor="zf-cli", payload=payload)


def test_integration_conflict_is_plan_level_arch():
    res = classify_rework_trigger(_ev({
        "status": "conflict",
        "conflict_files": ["cj-min/packages/state/db.ts"],
        "error": "cherry-pick failed: merge conflict",
    }))
    assert res.classification == "design_issue"
    assert res.suspected_owner == "arch"
    assert res.taxonomy_bucket == "content"


def test_integration_scope_overlap_is_plan_level_arch():
    res = classify_rework_trigger(_ev({
        "status": "failed",
        "error": "overlapping allowed paths between slices",
    }))
    assert res.classification == "design_issue"
    assert res.suspected_owner == "arch"


def test_integration_plain_build_failure_is_impl_level_dev():
    res = classify_rework_trigger(_ev({
        "status": "quality_failed",
        "error": "tsc: type error in handler",
    }))
    assert res.classification == "product_issue"
    assert res.suspected_owner == "dev"


def test_integration_conflict_marker_in_unrelated_field_does_not_misroute():
    # A clean build failure whose payload merely mentions "conflict" in an
    # unrelated field (target_ref / failing test name) must NOT be misrouted to
    # arch re-plan — only error/reason/message + structured signals count.
    res = classify_rework_trigger(_ev({
        "status": "quality_failed",
        "target_ref": "cand/fix-merge-conflict-handler",
        "failing_test": "test_merge_conflict_resolution",
        "error": "tsc: type error",
    }))
    assert res.classification == "product_issue"
    assert res.suspected_owner == "dev"


def test_candidate_conflict_is_plan_level_arch():
    # The real cherry-pick conflict event (candidates.py emits candidate.conflict,
    # not integration.failed) is a plan-level slice overlap → arch re-plan.
    ev = ZfEvent(type="candidate.conflict", actor="zf-cli", payload={
        "status": "conflict",
        "conflict_files": ["cj-min/packages/state/db.ts"],
    })
    res = classify_rework_trigger(ev)
    assert res.classification == "design_issue"
    assert res.suspected_owner == "arch"
