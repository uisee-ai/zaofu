"""Deterministic fanout recovery helpers.

These helpers are intentionally small wrappers around the existing
Orchestrator fanout recovery path. They detect a narrow durable-state gap:
a writer emitted ``dev.build.done`` / ``dev.failed`` / ``dev.blocked`` with
fanout identity, but the corresponding ``fanout.child.*`` terminal event was
not recorded because the watcher was down or missed the event.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from zf.core.config.schema import ZfConfig
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent


_WRITER_RESULT_EVENTS = {
    "dev.build.done",
    "dev.failed",
    "dev.blocked",
    "task.ref.updated",
}
_FANOUT_TERMINAL_EVENTS = {
    "fanout.child.completed",
    "fanout.child.failed",
}


@dataclass(frozen=True)
class FanoutRecoveryGap:
    fanout_id: str
    child_id: str
    result_event_id: str
    result_event_type: str
    task_id: str


@dataclass(frozen=True)
class FanoutRecoveryResult:
    candidates: tuple[FanoutRecoveryGap, ...] = ()
    events_appended: int = 0
    terminals_appended: int = 0

    @property
    def recovered(self) -> bool:
        return self.events_appended > 0 or self.terminals_appended > 0


def find_unrecorded_writer_fanout_results(
    *,
    state_dir: Path,
    events: Iterable[ZfEvent],
) -> tuple[FanoutRecoveryGap, ...]:
    """Return writer fanout result events missing child terminal records."""

    state_dir = Path(state_dir)
    terminal_sources: set[str] = set()
    terminal_children: set[tuple[str, str]] = set()
    event_list = list(events)
    for event in event_list:
        if event.type not in _FANOUT_TERMINAL_EVENTS:
            continue
        payload = event.payload if isinstance(event.payload, dict) else {}
        fanout_id = str(payload.get("fanout_id") or "")
        child_id = str(payload.get("child_id") or "")
        if fanout_id and child_id:
            terminal_children.add((fanout_id, child_id))
        result_event_id = str(payload.get("result_event_id") or "")
        if result_event_id:
            terminal_sources.add(result_event_id)
        if event.causation_id:
            terminal_sources.add(str(event.causation_id))

    gaps: list[FanoutRecoveryGap] = []
    seen: set[tuple[str, str, str]] = set()
    for event in event_list:
        if event.type not in _WRITER_RESULT_EVENTS:
            continue
        if event.id in terminal_sources:
            continue
        payload = event.payload if isinstance(event.payload, dict) else {}
        fanout_id = str(payload.get("fanout_id") or "")
        child_id = str(payload.get("child_id") or payload.get("child_run") or "")
        if not fanout_id or not child_id:
            continue
        if (fanout_id, child_id) in terminal_children:
            continue
        manifest = _read_writer_manifest(state_dir, fanout_id)
        if not manifest:
            continue
        child = _manifest_child(manifest, child_id)
        if not child:
            continue
        status = str(child.get("status") or "")
        if status == "completed":
            continue
        if status == "failed" and event.type not in {"dev.build.done", "task.ref.updated"}:
            continue
        key = (fanout_id, child_id, event.id)
        if key in seen:
            continue
        seen.add(key)
        gaps.append(FanoutRecoveryGap(
            fanout_id=fanout_id,
            child_id=child_id,
            result_event_id=event.id,
            result_event_type=event.type,
            task_id=str(event.task_id or payload.get("task_id") or child.get("task_id") or ""),
        ))
    return tuple(gaps)


def recover_unrecorded_writer_fanout_results(
    *,
    state_dir: Path,
    config: ZfConfig,
    project_root: Path | None = None,
    event_log: EventLog | None = None,
    transport: object | None = None,
) -> FanoutRecoveryResult:
    """Run the canonical Orchestrator fanout recovery when gaps exist."""

    state_dir = Path(state_dir)
    event_log = event_log or EventLog(state_dir / "events.jsonl")
    before = event_log.read_all()
    candidates = find_unrecorded_writer_fanout_results(
        state_dir=state_dir,
        events=before,
    )
    if not candidates:
        return FanoutRecoveryResult()

    terminal_before = _terminal_count(before)
    event_count_before = len(before)
    from zf.runtime.orchestrator import Orchestrator
    from zf.runtime.transport import make_transport

    orch = Orchestrator(
        state_dir,
        config,
        transport if transport is not None else make_transport(config),
        project_root=project_root,
    )
    orch._recover_unrecorded_writer_fanout_results()

    after = event_log.read_all()
    return FanoutRecoveryResult(
        candidates=candidates,
        events_appended=max(0, len(after) - event_count_before),
        terminals_appended=max(0, _terminal_count(after) - terminal_before),
    )


def _terminal_count(events: Iterable[ZfEvent]) -> int:
    return sum(1 for event in events if event.type in _FANOUT_TERMINAL_EVENTS)


def _read_writer_manifest(state_dir: Path, fanout_id: str) -> dict:
    if not fanout_id or "/" in fanout_id or "\\" in fanout_id:
        return {}
    path = state_dir / "fanouts" / fanout_id / "manifest.json"
    try:
        import json

        manifest = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(manifest, dict):
        return {}
    if manifest.get("topology") != "fanout_writer_scoped":
        return {}
    return manifest


def _manifest_child(manifest: dict, child_id: str) -> dict:
    for child in manifest.get("children", []) or []:
        if not isinstance(child, dict):
            continue
        if str(child.get("child_id") or "") == child_id:
            return child
    return {}
