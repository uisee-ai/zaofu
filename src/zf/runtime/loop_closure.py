"""Runtime bridge from downstream terminal events back into Loop closure."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.runtime.delivery_projection_common import payload
from zf.runtime.loop_learning import LOOP_LEARNING_MATERIALIZED, materialize_loop_learning_artifacts
from zf.runtime.loop_projection import build_loop_projection
from zf.runtime.loop_verify import LOOP_VERIFY_COMPLETED, LOOP_VERIFY_REQUESTED


LOOP_CLOSURE_SOURCE_EVENTS = frozenset({
    "repair.action.applied",
    "repair.action.rejected",
    "autoresearch.loop.completed",
    "autoresearch.loop.failed",
    "replan.contract_eval.completed",
    "worker.stuck.recovered",
    "static_gate.passed",
    "verify.passed",
    "test.passed",
    "judge.passed",
})


def append_loop_closure_events(
    *,
    events: Sequence[ZfEvent],
    source_event: ZfEvent,
    writer: EventWriter,
    state_dir: Path | None = None,
    project_id: str = "",
) -> list[ZfEvent]:
    """Append deterministic verify/learning events for one source event."""

    if source_event.type not in LOOP_CLOSURE_SOURCE_EVENTS:
        return []
    all_events = _with_source(events, source_event)
    projection = build_loop_projection(
        events=list(enumerate(all_events)),
        generated_at=source_event.ts or "",
        project_id=project_id,
    )
    emitted: list[ZfEvent] = []
    existing_verify_requests = _existing_ids(all_events, LOOP_VERIFY_REQUESTED, "verification_id")
    existing_verify_completed = _existing_ids(all_events, LOOP_VERIFY_COMPLETED, "verification_id")
    existing_learning = _existing_ids(all_events, LOOP_LEARNING_MATERIALIZED, "learning_id")

    related_verifications = _related_verifications(projection, source_event)
    for verification in related_verifications:
        verification_id = str(verification.get("verification_id") or "")
        if not verification_id:
            continue
        action_id = str(verification.get("action_id") or "")
        if verification_id not in existing_verify_requests:
            emitted.append(writer.append(ZfEvent(
                type=LOOP_VERIFY_REQUESTED,
                actor="zf-loop",
                task_id=_first(verification.get("task_ids")) or source_event.task_id,
                payload={
                    "verification_id": verification_id,
                    "action_id": action_id,
                    "source_action_id": verification.get("source_action_id") or action_id,
                    "loop_id": verification.get("loop_id") or "",
                    "candidate_id": verification.get("candidate_id") or "",
                    "terminal_event_id": verification.get("terminal_event_id") or source_event.id,
                    "terminal_event_type": verification.get("terminal_event_type") or source_event.type,
                    "reason": "loop action terminal evidence observed",
                    "evidence_refs": verification.get("evidence_refs") or [],
                    "missing_evidence": verification.get("missing_evidence") or [],
                    "next_check": verification.get("next_check") or "",
                },
                causation_id=source_event.id,
                correlation_id=source_event.correlation_id,
            )))
        status = str(verification.get("status") or "")
        if status in {"passed", "failed", "inconclusive"} and verification_id not in existing_verify_completed:
            emitted.append(writer.append(ZfEvent(
                type=LOOP_VERIFY_COMPLETED,
                actor="zf-loop",
                task_id=_first(verification.get("task_ids")) or source_event.task_id,
                payload={
                    "verification_id": verification_id,
                    "action_id": action_id,
                    "source_action_id": verification.get("source_action_id") or action_id,
                    "loop_id": verification.get("loop_id") or "",
                    "candidate_id": verification.get("candidate_id") or "",
                    "status": status,
                    "result": verification.get("result") or status,
                    "reason": verification.get("reason") or "",
                    "terminal_event_id": verification.get("terminal_event_id") or source_event.id,
                    "terminal_event_type": verification.get("terminal_event_type") or source_event.type,
                    "evidence_refs": verification.get("evidence_refs") or [],
                    "missing_evidence": verification.get("missing_evidence") or [],
                    "next_check": verification.get("next_check") or "",
                },
                causation_id=source_event.id,
                correlation_id=source_event.correlation_id,
            )))

    if state_dir is not None:
        written = materialize_loop_learning_artifacts(
            state_dir=state_dir,
            learning=[
                row for row in (projection.get("learning") or [])
                if isinstance(row, dict)
                and str(row.get("learning_id") or "") not in existing_learning
                and _learning_related_to_verifications(row, related_verifications)
            ],
        )
        for row in written:
            learning_id = str(row.get("learning_id") or "")
            if not learning_id:
                continue
            emitted.append(writer.append(ZfEvent(
                type=LOOP_LEARNING_MATERIALIZED,
                actor="zf-loop",
                task_id=_first(row.get("task_ids")) or source_event.task_id,
                payload={
                    "learning_id": learning_id,
                    "loop_id": row.get("loop_id") or "",
                    "candidate_id": row.get("candidate_id") or "",
                    "action_id": row.get("action_id") or "",
                    "verification_id": row.get("verification_id") or "",
                    "artifact_kind": row.get("artifact_kind") or "",
                    "artifact_ref": row.get("artifact_ref") or "",
                    "promotion_path": row.get("promotion_path") or "",
                    "status": "materialized",
                    "evidence_refs": row.get("evidence_refs") or [],
                },
                causation_id=source_event.id,
                correlation_id=source_event.correlation_id,
            )))
    return emitted


def _related_verifications(projection: dict[str, Any], source_event: ZfEvent) -> list[dict[str, Any]]:
    source_id = str(source_event.id or "")
    out: list[dict[str, Any]] = []
    for row in projection.get("verifications") or []:
        if not isinstance(row, dict):
            continue
        refs = set(_strings(row.get("event_ids")) + _strings(row.get("evidence_refs")))
        if source_id and (
            row.get("terminal_event_id") == source_id
            or row.get("completed_event_id") == source_id
            or source_id in refs
        ):
            out.append(row)
    return out


def _learning_related_to_verifications(row: dict[str, Any], verifications: list[dict[str, Any]]) -> bool:
    verification_ids = {str(item.get("verification_id") or "") for item in verifications}
    return str(row.get("verification_id") or "") in verification_ids


def _with_source(events: Sequence[ZfEvent], source_event: ZfEvent) -> list[ZfEvent]:
    if source_event.id and any(event.id == source_event.id for event in events):
        return list(events)
    return [*events, source_event]


def _existing_ids(events: Sequence[ZfEvent], event_type: str, key: str) -> set[str]:
    out: set[str] = set()
    for event in events:
        if event.type != event_type:
            continue
        data = payload(event)
        value = str(data.get(key) or "").strip()
        if value:
            out.add(value)
    return out


def _first(value: object) -> str:
    values = _strings(value)
    return values[0] if values else ""


def _strings(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if str(item)]
