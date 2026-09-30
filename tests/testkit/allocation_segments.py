"""Fail-closed planning and reconciliation for long allocation observations.

This module deliberately does not start/stop Memray.  A plan partitions the
business task range into audit segments while one continuous Tracker and one
capture file remain active for the whole run.  It is test infrastructure only:
it cannot turn a short calibration into a PERF-05 pass.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import math
from pathlib import Path
import re
from typing import Any, Iterable, Mapping


MAX_TASKS = 64
DEFAULT_BUSINESS_BUDGET_SECONDS = 1200.0
DEFAULT_CAPTURE_BYTES = 2 * 1024**3
DEFAULT_MIN_FREE_BYTES = 4 * 1024**3
REENTRY_SOURCE_KINDS = frozenset({
    "official_lossless_api",
    "public_complete_replay",
})


def _positive_int(value: object, name: str) -> int:
    if type(value) is not int or value <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return value


def _positive_float(value: object, name: str) -> float:
    if type(value) not in (int, float) or not math.isfinite(float(value)) or value <= 0:
        raise ValueError(f"{name} must be a finite positive number")
    return float(value)


@dataclass(frozen=True)
class AllocationSegment:
    """A task/checkpoint range; it never owns a separate Tracker."""

    index: int
    first_task: int
    last_task: int
    task_count: int
    required_windows: int = 1

    def __post_init__(self) -> None:
        _positive_int(self.index + 1, "index")
        _positive_int(self.first_task, "first_task")
        _positive_int(self.last_task, "last_task")
        _positive_int(self.task_count, "task_count")
        _positive_int(self.required_windows, "required_windows")
        if self.last_task < self.first_task:
            raise ValueError("segment task range is reversed")
        if self.last_task - self.first_task + 1 != self.task_count:
            raise ValueError("segment task_count disagrees with task range")


@dataclass(frozen=True)
class AllocationObservationPlan:
    """Immutable admission plan for a continuous allocation observation."""

    total_tasks: int
    segment_size: int
    business_budget_seconds: float = DEFAULT_BUSINESS_BUDGET_SECONDS
    max_capture_bytes: int = DEFAULT_CAPTURE_BYTES
    min_free_bytes: int = DEFAULT_MIN_FREE_BYTES
    tracker_lifecycle: str = "single_continuous"
    capture_mode: str = "single_file"
    segments: tuple[AllocationSegment, ...] = ()

    def __post_init__(self) -> None:
        _positive_int(self.total_tasks, "total_tasks")
        if self.total_tasks > MAX_TASKS:
            raise ValueError(f"total_tasks exceeds {MAX_TASKS}")
        _positive_int(self.segment_size, "segment_size")
        _positive_float(self.business_budget_seconds, "business_budget_seconds")
        _positive_int(self.max_capture_bytes, "max_capture_bytes")
        _positive_int(self.min_free_bytes, "min_free_bytes")
        if self.tracker_lifecycle != "single_continuous":
            raise ValueError("allocation evidence requires one continuous Tracker")
        if self.capture_mode != "single_file":
            raise ValueError("allocation evidence requires one capture file")
        validate_plan(self)

    def to_dict(self) -> dict[str, Any]:
        return {
            **asdict(self),
            "segments": [asdict(segment) for segment in self.segments],
            "scope": (
                "planning contract only; segments are checkpoints, not tracker restarts; "
                "does not prove PERF-05"
            ),
        }


@dataclass(frozen=True)
class AllocationReentryEvidence:
    """External evidence required before reopening the long PERF-05 sample."""

    source_kind: str
    reference: str
    sha256: str
    complete_window_replay: bool
    budget_compatible: bool


def admit_long_sample(evidence: AllocationReentryEvidence) -> dict[str, Any]:
    """Admit a long sample only with independently verifiable new evidence.

    The K552-F2 research explicitly says that changing paths, buffers, or
    sampling frequency is not a re-entry trigger.  This gate keeps that rule
    executable and fail-closed without starting Memray or changing budgets.
    """
    if evidence.source_kind not in REENTRY_SOURCE_KINDS:
        raise ValueError("unsupported re-entry evidence source")
    if not evidence.reference.strip():
        raise ValueError("re-entry evidence reference is required")
    if not isinstance(evidence.sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", evidence.sha256):
        raise ValueError("re-entry evidence sha256 must be a lowercase hex digest")
    if not evidence.complete_window_replay:
        raise ValueError("re-entry evidence must prove complete-window replay")
    if not evidence.budget_compatible:
        raise ValueError("re-entry evidence must preserve the existing budget")
    return {
        "status": "admitted",
        "source_kind": evidence.source_kind,
        "reference": evidence.reference,
        "sha256": evidence.sha256,
        "scope": "admission only; caller still owns the single-Tracker 32Task run",
    }


def build_plan(
    total_tasks: int,
    *,
    segment_size: int,
    business_budget_seconds: float = DEFAULT_BUSINESS_BUDGET_SECONDS,
    max_capture_bytes: int = DEFAULT_CAPTURE_BYTES,
    min_free_bytes: int = DEFAULT_MIN_FREE_BYTES,
    required_windows: int = 1,
) -> AllocationObservationPlan:
    """Build a fixed plan without adapting limits to observed output."""
    _positive_int(total_tasks, "total_tasks")
    _positive_int(segment_size, "segment_size")
    _positive_int(required_windows, "required_windows")
    if total_tasks > MAX_TASKS:
        raise ValueError(f"total_tasks exceeds {MAX_TASKS}")
    segments = tuple(
        AllocationSegment(
            index=index,
            first_task=start,
            last_task=min(start + segment_size - 1, total_tasks),
            task_count=min(segment_size, total_tasks - start + 1),
            required_windows=required_windows,
        )
        for index, start in enumerate(range(1, total_tasks + 1, segment_size))
    )
    return AllocationObservationPlan(
        total_tasks=total_tasks,
        segment_size=segment_size,
        business_budget_seconds=float(business_budget_seconds),
        max_capture_bytes=max_capture_bytes,
        min_free_bytes=min_free_bytes,
        segments=segments,
    )


def validate_plan(plan: AllocationObservationPlan) -> None:
    """Reject gaps, overlaps, reordered segments, or mutable tracker policy."""
    if not plan.segments:
        raise ValueError("allocation plan has no segments")
    expected = 1
    for index, segment in enumerate(plan.segments):
        if segment.index != index:
            raise ValueError("segment indexes must be contiguous")
        if segment.first_task != expected:
            raise ValueError("segment task ranges must have no gaps or overlaps")
        expected = segment.last_task + 1
        if segment.task_count > plan.segment_size:
            raise ValueError("segment exceeds fixed segment_size")
    if expected != plan.total_tasks + 1:
        raise ValueError("segments do not cover total_tasks")


def validate_observations(
    plan: AllocationObservationPlan,
    rows: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    """Reconcile checkpoint rows against the fixed plan, fail-closed.

    Each row is a checkpoint for one segment.  All rows must identify the same
    process, capture path and Tracker start; a new PID/capture/start marks a
    tracker restart and is rejected rather than silently stitching evidence.
    """
    observations = list(rows)
    if len(observations) != len(plan.segments):
        raise ValueError("observation count must equal planned segment count")
    expected_pid: int | None = None
    expected_capture: str | None = None
    expected_tracker_start: int | None = None
    seen: list[int] = []
    previous_window_end: int | None = None
    for expected_segment, row in zip(plan.segments, observations, strict=True):
        if not isinstance(row, Mapping):
            raise ValueError("observation row must be a mapping")
        if row.get("segment_index") != expected_segment.index:
            raise ValueError("observation segments are out of order")
        if row.get("first_task") != expected_segment.first_task or row.get("last_task") != expected_segment.last_task:
            raise ValueError("observation task range disagrees with plan")
        if row.get("completed_tasks") != expected_segment.last_task:
            raise ValueError("observation does not identify the completed task watermark")
        pid = row.get("tracker_pid")
        tracker_start = row.get("tracker_started_ns")
        capture = row.get("capture_path")
        if type(pid) is not int or pid <= 0:
            raise ValueError("observation has no valid tracker_pid")
        if type(tracker_start) is not int or tracker_start <= 0:
            raise ValueError("observation has no valid tracker_started_ns")
        if not isinstance(capture, str) or not capture or Path(capture).name in {"", ".", ".."}:
            raise ValueError("observation has no valid capture_path")
        window = row.get("window")
        if not isinstance(window, Mapping):
            raise ValueError("observation is missing its stable window")
        start = window.get("wall_start_ns")
        end = window.get("wall_end_ns")
        if type(start) is not int or type(end) is not int or not start < end:
            raise ValueError("observation window is not strictly forward")
        if previous_window_end is not None and start <= previous_window_end:
            raise ValueError("observation windows overlap or move backwards")
        if expected_pid is None:
            expected_pid, expected_capture, expected_tracker_start = pid, capture, tracker_start
        elif (pid, capture, tracker_start) != (expected_pid, expected_capture, expected_tracker_start):
            raise ValueError("observations do not share one continuous tracker/capture")
        previous_window_end = end
        seen.append(expected_segment.index)
    return {
        "schema_version": "zf-testkit-allocation-segments.v1",
        "status": "reconciled",
        "tracker_lifecycle": plan.tracker_lifecycle,
        "capture_mode": plan.capture_mode,
        "segment_indexes": seen,
        "total_tasks": plan.total_tasks,
        "business_budget_seconds": plan.business_budget_seconds,
        "max_capture_bytes": plan.max_capture_bytes,
        "scope": "checkpoint continuity only; no PERF-05 or leak verdict",
    }
