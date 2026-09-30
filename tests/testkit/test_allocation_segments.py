"""Deterministic contracts for K552-F2 segmented observation planning."""

from __future__ import annotations

from dataclasses import replace

import pytest

from tests.testkit.allocation_segments import (
    AllocationReentryEvidence,
    admit_long_sample,
    build_plan,
    validate_observations,
)


def _rows(plan, *, pid=1234, capture="/tmp/continuous/memray.bin", start=10_000):
    return [
        {
            "segment_index": segment.index,
            "first_task": segment.first_task,
            "last_task": segment.last_task,
            "completed_tasks": segment.last_task,
            "tracker_pid": pid,
            "tracker_started_ns": 1_000,
            "capture_path": capture,
            "window": {
                "wall_start_ns": start + segment.index * 100,
                "wall_end_ns": start + segment.index * 100 + 50,
            },
        }
        for segment in plan.segments
    ]


def test_plan_is_contiguous_and_single_tracker():
    plan = build_plan(7, segment_size=3)
    assert [(s.first_task, s.last_task) for s in plan.segments] == [(1, 3), (4, 6), (7, 7)]
    assert plan.tracker_lifecycle == "single_continuous"
    assert plan.capture_mode == "single_file"
    assert plan.business_budget_seconds == 1200.0


def test_observations_reconcile_only_when_one_tracker_and_capture_are_shared():
    plan = build_plan(4, segment_size=2)
    result = validate_observations(plan, _rows(plan))
    assert result["status"] == "reconciled"
    assert result["segment_indexes"] == [0, 1]
    assert result["scope"].startswith("checkpoint continuity")


@pytest.mark.parametrize("mutation", ["missing", "gap", "bad_watermark", "restart_pid", "restart_capture", "restart_tracker", "bad_window", "overlap"])
def test_observation_rejects_incomplete_or_stitched_evidence(mutation):
    plan = build_plan(4, segment_size=2)
    rows = _rows(plan)
    if mutation == "missing":
        rows.pop()
    elif mutation == "gap":
        rows[1]["first_task"] = 4
    elif mutation == "bad_watermark":
        rows[1]["completed_tasks"] = 3
    elif mutation == "restart_pid":
        rows[1]["tracker_pid"] = 5678
    elif mutation == "restart_capture":
        rows[1]["capture_path"] = "/tmp/other/memray.bin"
    elif mutation == "restart_tracker":
        rows[1]["tracker_started_ns"] = 2_000
    elif mutation == "bad_window":
        rows[0]["window"]["wall_end_ns"] = rows[0]["window"]["wall_start_ns"]
    else:
        rows[1]["window"]["wall_start_ns"] = rows[0]["window"]["wall_end_ns"]
    with pytest.raises(ValueError):
        validate_observations(plan, rows)


@pytest.mark.parametrize("kwargs", [
    {"total_tasks": 0, "segment_size": 1},
    {"total_tasks": 65, "segment_size": 1},
    {"total_tasks": 4, "segment_size": 0},
    {"total_tasks": 4, "segment_size": 2, "required_windows": 0},
])
def test_plan_rejects_unbounded_or_empty_inputs(kwargs):
    with pytest.raises(ValueError):
        build_plan(**kwargs)


def test_plan_limits_are_not_adapted_to_observation_rows():
    plan = build_plan(4, segment_size=2, max_capture_bytes=100, min_free_bytes=200)
    rows = _rows(plan)
    rows[0]["capture_bytes"] = 10_000_000
    with pytest.raises(ValueError, match="observation"):
        # A row with unrecognized/unsanctioned budget facts cannot mutate the plan.
        rows[0]["capture_path"] = ""
        validate_observations(plan, rows)
    assert plan.max_capture_bytes == 100
    assert plan.min_free_bytes == 200


def test_long_sample_reentry_requires_independent_complete_window_evidence():
    result = admit_long_sample(AllocationReentryEvidence(
        source_kind="official_lossless_api",
        reference="research/allocations-v2.md",
        sha256="a" * 64,
        complete_window_replay=True,
        budget_compatible=True,
    ))
    assert result["status"] == "admitted"
    assert result["scope"].startswith("admission only")


@pytest.mark.parametrize("mutation", [
    "unsupported_source", "missing_reference", "bad_digest",
    "partial_window", "changed_budget",
])
def test_long_sample_reentry_rejects_shortcuts_and_unproven_evidence(mutation):
    evidence = AllocationReentryEvidence(
        source_kind="official_lossless_api",
        reference="research/allocations-v2.md",
        sha256="b" * 64,
        complete_window_replay=True,
        budget_compatible=True,
    )
    if mutation == "unsupported_source":
        evidence = replace(evidence, source_kind="buffered_capture")
    elif mutation == "missing_reference":
        evidence = replace(evidence, reference=" ")
    elif mutation == "bad_digest":
        evidence = replace(evidence, sha256="not-a-digest")
    elif mutation == "partial_window":
        evidence = replace(evidence, complete_window_replay=False)
    else:
        evidence = replace(evidence, budget_compatible=False)
    with pytest.raises(ValueError):
        admit_long_sample(evidence)
