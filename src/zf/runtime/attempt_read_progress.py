"""Measure attempt-local required-read coverage for bounded worker recovery."""

from __future__ import annotations

from pathlib import Path
import shlex
from typing import Any, Mapping

from zf.runtime.artifact_read_ledger import (
    ArtifactReadError,
    _parse_ledger,
    _read_matches,
    active_read_ledger_path,
)
from zf.runtime.artifact_read_requirements import required_reads_for_attempt
from zf.runtime.cli_command import zf_cli_cmd


def required_read_progress(
    state_dir: Path, events: list[Any], *, attempt_id: str, operation_id: str,
) -> int | None:
    """Count unique required content covered, not reads or duplicated pages.

    This is a liveness signal only; result admission still validates the full
    ledger. Unreadable or inconsistent evidence never grants another nudge.
    """
    try:
        requirements = required_reads_for_attempt(
            events, attempt_id, state_dir=state_dir, operation_id=operation_id,
        )
        active = active_read_ledger_path(state_dir, attempt_id)
        files = sorted(active.parent.glob("read-ledger-*.jsonl"))
        if active.exists():
            files.append(active)
        rows = [row for path in files for row in _parse_ledger(
            path.read_bytes(), expected_attempt_id=attempt_id,
        )]
        return sum(_coverage(rows, requirement) for requirement in requirements)
    except (OSError, ValueError, TypeError, KeyError, ArtifactReadError):
        return None


def progress_nudge_state(
    runtime: Any, *, role_instance: str, task_id: str, dispatch_id: str,
) -> tuple[str, int, bool] | None:
    """Allow a further nudge only after unique progress and a closed turn."""
    from zf.runtime.task_pipeline_attempt_recovery import live_task_pipeline_attempt_for_role

    try:
        attempt = live_task_pipeline_attempt_for_role(runtime, role_instance)
        if not attempt or attempt.get("status") != "sent":
            return None
        if attempt.get("task_id") != task_id or attempt.get("dispatch_id") != dispatch_id:
            return None
        attempt_id = str(attempt.get("attempt_id") or "")
        operation_id = str(attempt.get("operation_id") or "")
        if not attempt_id or not operation_id:
            return None
        events = runtime.event_log.read_all()
        progress = required_read_progress(
            Path(runtime.state_dir), events, attempt_id=attempt_id, operation_id=operation_id,
        )
        if progress is None:
            return attempt_id, 0, False
        nudges = [
            (i, event) for i, event in enumerate(events)
            if event.type == "worker.stuck.recovered" and event.actor == role_instance
            and event.task_id == task_id and event.payload.get("dispatch_id") == dispatch_id
            and event.payload.get("recovery_action") == "completion_nudge_requested"
        ]
        if not nudges:
            return attempt_id, progress, True
        index, last = nudges[-1]
        previous = last.payload.get("input_read_progress")
        if last.payload.get("attempt_id") != attempt_id or not isinstance(previous, int):
            return attempt_id, progress, False
        activity = [event.type for event in events[index + 1:]
                    if event.actor == role_instance and event.type in {
                        "provider.turn.closed", "codex.hook.pre_tool_use", "agent.text",
                    }]
        return attempt_id, progress, bool(
            progress > previous and activity and activity[-1] == "provider.turn.closed"
        )
    except (OSError, ValueError, TypeError, KeyError):
        return None


def input_continuation_lines(runtime: Any, *, role_instance: str, task_id: str, dispatch_id: str) -> list[str]:
    from zf.runtime.task_pipeline_attempt_recovery import live_task_pipeline_attempt_for_role

    attempt = live_task_pipeline_attempt_for_role(runtime, role_instance)
    if (not attempt or attempt.get("task_id") != task_id
            or attempt.get("dispatch_id") != dispatch_id or attempt.get("status") != "sent"):
        return []
    attempt_id = str(attempt.get("attempt_id") or "")
    if not attempt_id:
        return []
    return [
        "", "## Required input continuation",
        f"This is still attempt `{attempt_id}`. Run `{zf_cli_cmd()} attempt "
        f"missing-reads --state-dir {shlex.quote(str(runtime.state_dir))} "
        f"{shlex.quote(attempt_id)}` and complete every missing required "
        "input using the original briefing's controlled pagination. "
        "Continue from previously recorded pages and keep reading remaining pages "
        "in this turn until complete or a concrete blocker prevents progress; "
        "do not stop merely after an arbitrary number of pages. Do not submit a result "
        "claiming completion while required reads are missing.",
    ]


def _coverage(rows: list[Mapping[str, Any]], requirement: Mapping[str, Any]) -> int:
    complete = 0
    groups: dict[tuple[str, int, int], list[tuple[int, int]]] = {}
    for row in rows:
        if _read_matches(row, requirement):
            complete = max(complete, int(row.get("returned_bytes") or 0))
            continue
        if "page_start" not in row or row.get("status") != "read":
            continue
        if any(str(row.get(k) or "") != str(requirement.get(k) or "")
               for k in ("source_id", "artifact_id", "artifact_sha256", "json_path")):
            continue
        start, end, total = (int(row[k]) for k in ("page_start", "page_end", "page_total"))
        selected_bytes = int(row.get("selected_bytes") or 0)
        digest = str(row.get("selected_sha256") or "")
        if (len(digest) != 64 or start < 0 or end <= start or end > total
                or selected_bytes < total or int(row.get("returned_bytes") or 0) <= 0):
            continue
        groups.setdefault((digest, total, selected_bytes), []).append((start, end))
    for intervals in groups.values():
        coverage = 0
        end = 0
        for start, stop in sorted(intervals):
            coverage += max(0, stop - max(start, end))
            end = max(end, stop)
        complete = max(complete, coverage)
    return complete
