"""Domain assertions shared by ZaoFu system tests."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, Iterable, Mapping

from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent

from .evidence import evidence_ref, sha256_file


class Verdict(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    NOT_APPLICABLE = "not_applicable"
    HARNESS_FAILURE = "harness_failure"
    FLAKY = "flaky"


@dataclass(frozen=True)
class TruthSnapshot:
    events: tuple[dict[str, Any], ...]
    state: Mapping[str, Any]
    evidence: tuple[dict[str, str], ...] = ()


def read_events(log: EventLog) -> tuple[ZfEvent, ...]:
    return tuple(log.read_all())


def assert_required_events(log: EventLog, event_types: Iterable[str]) -> None:
    actual = {event.type for event in read_events(log)}
    missing = [item for item in event_types if item not in actual]
    assert not missing, f"required events missing: {missing}; actual={sorted(actual)}"


def assert_forbidden_events(log: EventLog, event_types: Iterable[str]) -> None:
    forbidden = set(event_types)
    actual = [event.type for event in read_events(log) if event.type in forbidden]
    assert not actual, f"forbidden events found: {actual}"


def assert_event_causation(
    log: EventLog,
    *,
    event_type: str,
    causation_id: str,
) -> None:
    matches = [
        event
        for event in read_events(log)
        if event.type == event_type and event.causation_id == causation_id
    ]
    assert matches, f"no {event_type!r} caused by {causation_id!r}"


def assert_evidence(
    path: Path,
    *,
    expected_digest: str | None = None,
    root: Path | None = None,
) -> dict[str, str]:
    assert path.exists(), f"evidence does not exist: {path}"
    assert path.is_file(), f"evidence is not a file: {path}"
    digest = sha256_file(path)
    if expected_digest is not None:
        assert digest == expected_digest, (
            f"evidence digest mismatch for {path}: {digest} != {expected_digest}"
        )
    return evidence_ref(path, root=root)


def assert_no_duplicate_event_ids(log: EventLog) -> None:
    events = read_events(log)
    ids = [event.id for event in events]
    duplicates = sorted({event_id for event_id in ids if ids.count(event_id) > 1})
    assert not duplicates, f"duplicate event ids: {duplicates}"


def classify_failure(
    *,
    product_assertion_failed: bool = False,
    environment_error: bool = False,
    flaky: bool = False,
) -> Verdict:
    if flaky:
        return Verdict.FLAKY
    if environment_error:
        return Verdict.HARNESS_FAILURE
    if product_assertion_failed:
        return Verdict.FAIL
    return Verdict.PASS
