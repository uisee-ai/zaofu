"""Canonical snapshots for replay and recovery comparisons."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from zf.core.events.log import EventLog
from .evidence import write_json_atomic
from .oracles import TruthSnapshot, read_events


def snapshot_event_log(log: EventLog) -> tuple[dict[str, Any], ...]:
    return tuple(
        {
            "id": event.id,
            "type": event.type,
            "task_id": event.task_id,
            "actor": event.actor,
            "payload": event.payload,
            "causation_id": event.causation_id,
            "correlation_id": event.correlation_id,
            "origin": event.origin,
        }
        for event in read_events(log)
    )

def truth_snapshot(
    log: EventLog,
    *,
    state: Mapping[str, Any] | None = None,
    evidence: tuple[dict[str, str], ...] = (),
) -> TruthSnapshot:
    return TruthSnapshot(
        events=snapshot_event_log(log),
        state=dict(state or {}),
        evidence=evidence,
    )


def assert_snapshot_equal(
    expected: TruthSnapshot,
    actual: TruthSnapshot,
) -> None:
    assert actual == expected, (
        "truth snapshot differs: "
        f"expected={expected!r}, actual={actual!r}"
    )


def persist_snapshot(path: Path, snapshot: TruthSnapshot) -> str:
    return write_json_atomic(
        path,
        {
            "events": list(snapshot.events),
            "state": dict(snapshot.state),
            "evidence": list(snapshot.evidence),
        },
    )
