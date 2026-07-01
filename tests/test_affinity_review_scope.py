"""B3 (R20): per-lane review scope discipline in the affinity child briefing.

R20: review-lanes reviewing the 213-file candidate hit worker.context.warning →
context.compact.failed → 2400s timeout → spurious review.rejected, because each
lane reviewed the WHOLE candidate instead of its affinity slice. The briefing now
carries an explicit "your slice only — do not read the full candidate" section
for affinity_stage_slots children (review + verify lanes), keeping per-lane
context bounded. The other lanes + the synth role cover the rest.
"""
from __future__ import annotations

from zf.runtime.affinity_review_scope import (
    affinity_scope_briefing_lines,
    affinity_scope_identity_errors,
)


def test_affinity_payload_returns_scope_section():
    lines = affinity_scope_briefing_lines({
        "assignment_strategy": "affinity_stage_slots",
        "lane_id": "lane0",
        "affinity_tag": "CJMIN-R20-PI-CORE",
    })
    text = "\n".join(lines)
    assert "AFFINITY SLICE ONLY" in text
    assert "lane0" in text and "CJMIN-R20-PI-CORE" in text
    assert "Do NOT read" in text and "full candidate" in text


def test_non_affinity_returns_empty():
    assert affinity_scope_briefing_lines({"assignment_strategy": "broadcast"}) == []
    assert affinity_scope_briefing_lines({}) == []
    assert affinity_scope_briefing_lines(None) == []


def test_affinity_without_lane_or_tag_returns_empty():
    assert affinity_scope_briefing_lines({"assignment_strategy": "affinity_stage_slots"}) == []


def test_falls_back_to_task_id_when_no_affinity_tag():
    lines = affinity_scope_briefing_lines({
        "assignment_strategy": "affinity_stage_slots",
        "lane_id": "lane1",
        "task_id": "CJMIN-R20-STATE-CONFIG",
    })
    assert "CJMIN-R20-STATE-CONFIG" in "\n".join(lines)


def test_affinity_identity_errors_require_lane_role_and_stage_slot():
    errors = affinity_scope_identity_errors({
        "assignment_strategy": "affinity_stage_slots",
    })

    assert errors == [
        "missing_lane_id",
        "missing_role_instance",
        "missing_stage_slot",
    ]


def test_affinity_identity_errors_accept_complete_identity():
    errors = affinity_scope_identity_errors(
        {
            "assignment_strategy": "affinity_stage_slots",
            "lane_id": "lane1",
            "stage_slot": "review",
        },
        role_instance="review-lane-1",
    )

    assert errors == []
