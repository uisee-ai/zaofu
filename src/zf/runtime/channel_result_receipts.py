"""Exact-origin, restart-safe result receipts for Channel workflows."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.core.security.redaction import redact_obj
from zf.core.state.atomic_io import atomic_write_text
from zf.runtime.channel_projection import project_channel
from zf.runtime.sidecar_refs import write_sidecar_json


CHANNEL_RESULT_RECEIPT_SCHEMA_VERSION = "channel-result-receipt.v1"
CHANNEL_RESULT_RECEIPT_RECORDED = "channel.result.receipt.recorded"
CHANNEL_RESULT_RECEIPT_FAILED = "channel.result.receipt.failed"
_RECONCILE_CURSOR_SCHEMA_VERSION = "channel-result-receipt-cursor.v1"
_MAX_DELIVERY_ATTEMPTS = 3
_SOURCE_EVENT_TYPES = frozenset({
    "channel.consensus.reached",
    "task.created",
    "workflow.result.available",
    "run.goal.completed",
    "run.goal.blocked",
    "ship.completed",
    "ship.done",
})


@dataclass(frozen=True)
class ChannelReceiptReconcileResult:
    considered: int = 0
    recorded: int = 0
    failed: int = 0
    attention: int = 0

    @property
    def changed(self) -> bool:
        return bool(self.recorded or self.failed or self.attention)


def reconcile_channel_result_receipts(
    *,
    state_dir: Path,
    event_log: EventLog,
    writer: EventWriter,
) -> ChannelReceiptReconcileResult:
    """Reconcile result facts into exact Channel/thread read receipts.

    The event ledger remains the source of occurrence and ordering. The
    required sidecar contains the complete receipt body; the Channel event
    carries only identity and a verified ref/digest.
    """

    state_dir = Path(state_dir)
    events = event_log.read_all()
    recorded = failed = attention = considered = 0
    for source in events:
        if source.type not in _SOURCE_EVENT_TYPES:
            continue
        candidate = _receipt_candidate(source, events)
        if candidate is None:
            continue
        considered += 1
        outcome = record_channel_result_receipt(
            state_dir=state_dir,
            writer=writer,
            source_event=source,
            candidate=candidate,
        )
        if outcome == "recorded":
            recorded += 1
        elif outcome == "failed":
            failed += 1
        elif outcome == "attention":
            attention += 1
    _write_cursor(
        state_dir,
        events=events,
        considered=considered,
        recorded=recorded,
        failed=failed,
        attention=attention,
    )
    return ChannelReceiptReconcileResult(
        considered=considered,
        recorded=recorded,
        failed=failed,
        attention=attention,
    )


def record_channel_result_receipt(
    *,
    state_dir: Path,
    writer: EventWriter,
    source_event: ZfEvent,
    candidate: dict[str, Any],
) -> str:
    """Persist and publish one exact-origin receipt.

    Returns ``recorded``, ``duplicate``, ``failed``, or ``attention``.
    """

    channel_id = str(candidate.get("channel_id") or "").strip()
    thread_id = str(candidate.get("thread_id") or "main").strip() or "main"
    source_digest = str(
        candidate.get("artifact_digest")
        or _event_digest(source_event)
    ).removeprefix("sha256:")
    revision = int(candidate.get("revision") or 1)
    receipt_kind = str(candidate.get("receipt_kind") or "").strip()
    idempotency_key = _receipt_idempotency_key(
        channel_id=channel_id,
        thread_id=thread_id,
        receipt_kind=receipt_kind,
        source_digest=source_digest,
        revision=revision,
    )
    legacy_key = idempotency_key
    task_id = str(candidate.get("task_id") or source_event.task_id or "")
    if receipt_kind == "task_created" and task_id:
        idempotency_key += f"|task:{task_id}"
    receipt_id = "channel-receipt-" + hashlib.sha1(
        idempotency_key.encode("utf-8")
    ).hexdigest()[:16]

    events = writer.event_log.read_all()
    if _recorded_event(events, idempotency_key) is not None:
        return "duplicate"
    # Older receipts used only PRD identity. Preserve the original Task's
    # receipt while allowing other Tasks from that same PRD to reconcile.
    if receipt_kind == "task_created" and task_id:
        legacy = _recorded_event(events, legacy_key)
        if legacy is not None and str(
            legacy.payload.get("task_id") or legacy.task_id or ""
        ) == task_id:
            return "duplicate"

    validation_error = _origin_validation_error(
        Path(state_dir),
        channel_id=channel_id,
        thread_id=thread_id,
    )
    if validation_error:
        return _record_failure_or_attention(
            writer=writer,
            source_event=source_event,
            receipt_id=receipt_id,
            idempotency_key=idempotency_key,
            candidate=candidate,
            reason=validation_error,
        )

    origin_binding = _receipt_origin_binding(
        Path(state_dir),
        events,
        channel_id=channel_id,
        thread_id=thread_id,
    )
    body = redact_obj({
        "schema_version": CHANNEL_RESULT_RECEIPT_SCHEMA_VERSION,
        "receipt_id": receipt_id,
        "receipt_kind": receipt_kind,
        "status": str(candidate.get("status") or "available"),
        "channel_id": channel_id,
        "thread_id": thread_id,
        "origin_binding": origin_binding,
        "source_event_id": source_event.id,
        "source_event_type": source_event.type,
        "artifact_ref": str(
            candidate.get("artifact_ref") or f"event:{source_event.id}"
        ),
        "artifact_digest": source_digest,
        "revision": revision,
        "task_id": str(candidate.get("task_id") or source_event.task_id or ""),
        "workflow_run_id": str(candidate.get("workflow_run_id") or ""),
        "delivery_id": str(candidate.get("delivery_id") or ""),
        "summary": str(candidate.get("summary") or ""),
        "links": (
            candidate.get("links")
            if isinstance(candidate.get("links"), dict)
            else {}
        ),
        "source_refs": (
            candidate.get("source_refs")
            if isinstance(candidate.get("source_refs"), list)
            else []
        ),
        "idempotency_key": idempotency_key,
    })
    descriptor = write_sidecar_json(
        Path(state_dir),
        (
            PurePosixPath("channels")
            / _safe_segment(channel_id)
            / "receipts"
            / f"{receipt_id}.json"
        ),
        body,
        kind="channel_result_receipt",
        schema_version=CHANNEL_RESULT_RECEIPT_SCHEMA_VERSION,
        created_by="zf-channel-result-reconciler",
        source_event_id=source_event.id,
        access_scope={
            "visibility": "project",
            "channel_id": channel_id,
            "thread_id": thread_id,
        },
        retention={"class": "audit_required"},
        required=True,
        preview=str(body.get("summary") or receipt_kind)[:240],
    )
    writer.emit(
        CHANNEL_RESULT_RECEIPT_RECORDED,
        actor="zf-channel-result-reconciler",
        task_id=str(body.get("task_id") or "") or None,
        causation_id=source_event.id,
        correlation_id=channel_id,
        payload={
            "schema_version": CHANNEL_RESULT_RECEIPT_SCHEMA_VERSION,
            "receipt_id": receipt_id,
            "receipt_kind": receipt_kind,
            "status": str(body.get("status") or "available"),
            "channel_id": channel_id,
            "thread_id": thread_id,
            "source_event_id": source_event.id,
            "source_event_type": source_event.type,
            "receipt_ref": descriptor["ref"],
            "receipt_digest": descriptor["sha256"],
            "artifact_ref": str(body.get("artifact_ref") or ""),
            "artifact_digest": source_digest,
            "revision": revision,
            "task_id": str(body.get("task_id") or ""),
            "workflow_run_id": str(body.get("workflow_run_id") or ""),
            "delivery_id": str(body.get("delivery_id") or ""),
            "idempotency_key": idempotency_key,
            "origin_binding": origin_binding,
            "links": body.get("links") or {},
            "source": "runtime",
        },
    )
    return "recorded"


def _receipt_candidate(
    source: ZfEvent,
    events: list[ZfEvent],
) -> dict[str, Any] | None:
    payload = source.payload if isinstance(source.payload, dict) else {}
    if source.type == "channel.consensus.reached":
        return {
            "receipt_kind": "prd_confirmed",
            "status": "confirmed",
            "channel_id": str(payload.get("channel_id") or ""),
            "thread_id": str(payload.get("thread_id") or "main"),
            "artifact_ref": str(
                payload.get("prd_ref") or payload.get("artifact_ref") or ""
            ),
            "artifact_digest": str(
                payload.get("prd_digest")
                or payload.get("artifact_digest")
                or ""
            ),
            "revision": int(payload.get("prd_revision") or 1),
            "summary": "Canonical Channel PRD confirmed.",
            "links": {
                "prd_ref": str(
                    payload.get("prd_ref")
                    or payload.get("artifact_ref")
                    or ""
                ),
                "readiness_ref": str(payload.get("readiness_ref") or ""),
                "conclusion_ref": str(payload.get("conclusion_ref") or ""),
            },
        }
    if source.type == "workflow.result.available":
        origin = (
            payload.get("origin_binding")
            if isinstance(payload.get("origin_binding"), dict)
            else {}
        )
        if str(origin.get("surface") or "") != "channel":
            return None
        return {
            "receipt_kind": "workflow_terminal",
            "status": str(payload.get("status") or "available"),
            "channel_id": str(origin.get("channel_id") or ""),
            "thread_id": str(origin.get("thread_id") or "main"),
            "artifact_ref": str(payload.get("artifact_ref") or ""),
            "artifact_digest": str(payload.get("artifact_digest") or ""),
            "revision": int(payload.get("request_revision") or 1),
            "task_id": str(payload.get("task_id") or source.task_id or ""),
            "workflow_run_id": str(payload.get("workflow_run_id") or ""),
            "summary": str(payload.get("summary") or ""),
            "links": {
                "workflow_result_event_id": source.id,
                "workflow_run_id": str(
                    payload.get("workflow_run_id") or ""
                ),
            },
        }

    authority: dict[str, Any] = {}
    task_id = str(source.task_id or payload.get("task_id") or "").strip()
    if source.type == "task.created":
        request = (
            payload.get("request")
            if isinstance(payload.get("request"), dict)
            else {}
        )
        authority = (
            request.get("channel_authority")
            if isinstance(request.get("channel_authority"), dict)
            else {}
        )
    else:
        task_id = task_id or _task_id_for_terminal(source, events)
        authority = _channel_authority_for_task(task_id, events)
    if not authority:
        return None

    source_artifact = (
        (
            payload.get("request")
            if isinstance(payload.get("request"), dict)
            else {}
        ).get("source_artifact")
        if source.type == "task.created"
        else {}
    )
    source_artifact = (
        source_artifact if isinstance(source_artifact, dict) else {}
    )
    receipt_kind = {
        "task.created": "task_created",
        "run.goal.completed": "workflow_terminal",
        "run.goal.blocked": "workflow_terminal",
        "ship.completed": "delivery_terminal",
        "ship.done": "delivery_terminal",
    }.get(source.type, "")
    if not receipt_kind:
        return None
    explicit_artifact_digest = _first_text(
        payload,
        "goal_dossier_digest",
        "delivery_digest",
        "artifact_digest",
        "result_digest",
    )
    if source.type == "task.created":
        artifact_digest = str(
            explicit_artifact_digest
            or source_artifact.get("digest")
            or authority.get("source_digest")
            or _event_digest(source)
        )
    else:
        # A terminal result is a new fact. Its identity must not collapse onto
        # the source PRD digest merely because the producer omitted a result
        # artifact; use the terminal event itself as the stable fallback.
        artifact_digest = str(
            explicit_artifact_digest or _event_digest(source)
        )
    workflow_run_id = str(
        payload.get("workflow_run_id") or payload.get("run_id") or ""
    )
    return {
        "receipt_kind": receipt_kind,
        "status": _source_status(source),
        "channel_id": str(authority.get("channel_id") or ""),
        "thread_id": str(authority.get("thread_id") or "main"),
        "artifact_ref": str(
            _first_text(
                payload,
                "goal_dossier_ref",
                "delivery_ref",
                "artifact_ref",
                "result_ref",
            )
            or source_artifact.get("ref")
            or authority.get("source_ref")
            or f"event:{source.id}"
        ),
        "artifact_digest": artifact_digest,
        "revision": int(authority.get("prd_revision") or 1),
        "task_id": task_id,
        "workflow_run_id": workflow_run_id,
        "delivery_id": str(
            payload.get("delivery_id")
            or payload.get("ship_id")
            or ""
        ),
        "summary": str(
            payload.get("summary")
            or (
                f"Task {task_id} created from confirmed Channel PRD."
                if source.type == "task.created"
                else f"{source.type} recorded for Task {task_id}."
            )
        ),
        "links": {
            "task_id": task_id,
            "workflow_run_id": workflow_run_id,
            "source_event_id": source.id,
        },
    }


def _task_id_for_terminal(
    source: ZfEvent,
    events: list[ZfEvent],
) -> str:
    payload = source.payload if isinstance(source.payload, dict) else {}
    run_ids = {
        str(value).strip()
        for value in (
            payload.get("workflow_run_id"),
            payload.get("run_id"),
            source.correlation_id,
        )
        if str(value or "").strip()
    }
    for event in reversed(events):
        if event.id == source.id:
            continue
        if source.correlation_id and event.correlation_id == source.correlation_id:
            if event.task_id:
                return str(event.task_id)
        event_payload = (
            event.payload if isinstance(event.payload, dict) else {}
        )
        if run_ids.intersection({
            str(event_payload.get("workflow_run_id") or "").strip(),
            str(event_payload.get("run_id") or "").strip(),
        }) and event.task_id:
            return str(event.task_id)
    return ""


def _channel_authority_for_task(
    task_id: str,
    events: list[ZfEvent],
) -> dict[str, Any]:
    if not task_id:
        return {}
    for event in reversed(events):
        if event.type != "task.created" or str(event.task_id or "") != task_id:
            continue
        payload = event.payload if isinstance(event.payload, dict) else {}
        request = (
            payload.get("request")
            if isinstance(payload.get("request"), dict)
            else {}
        )
        authority = request.get("channel_authority")
        return dict(authority) if isinstance(authority, dict) else {}
    return {}


def _origin_validation_error(
    state_dir: Path,
    *,
    channel_id: str,
    thread_id: str,
) -> str:
    if not channel_id:
        return "result receipt requires exact channel_id"
    channel = project_channel(state_dir, channel_id)
    if channel is None:
        return "result receipt Channel origin no longer exists"
    known_threads = {
        str(item.get("thread_id") or "main")
        for item in channel.get("threads") or []
        if isinstance(item, dict)
    }
    consensus = (
        channel.get("consensus")
        if isinstance(channel.get("consensus"), dict)
        else {}
    )
    if thread_id not in known_threads and thread_id not in consensus:
        return "result receipt thread origin does not exist"
    return ""


def _receipt_origin_binding(
    state_dir: Path,
    events: list[ZfEvent],
    *,
    channel_id: str,
    thread_id: str,
) -> dict[str, Any]:
    """Resolve one exact external return target without latest-thread fallback."""

    for event in events:
        if event.type != "channel.message.posted":
            continue
        payload = event.payload if isinstance(event.payload, dict) else {}
        if str(payload.get("channel_id") or event.correlation_id or "") != channel_id:
            continue
        if str(payload.get("thread_id") or "main") != thread_id:
            continue
        refs = payload.get("refs") if isinstance(payload.get("refs"), dict) else {}
        feishu = refs.get("feishu") if isinstance(refs.get("feishu"), dict) else {}
        chat_id = str(feishu.get("chat_id") or "").strip()
        message_id = str(feishu.get("message_id") or "").strip()
        root_message_id = str(feishu.get("root_message_id") or "").strip()
        parent_message_id = str(feishu.get("parent_message_id") or "").strip()
        if chat_id and message_id:
            return {
                "schema_version": "channel-origin-binding.v1",
                "surface": "feishu",
                "channel_id": channel_id,
                "thread_id": thread_id,
                "chat_id": chat_id,
                "origin_message_id": (
                    root_message_id or parent_message_id or message_id
                ),
                "root_message_id": root_message_id,
                "source_message_id": message_id,
            }
    channel = project_channel(state_dir, channel_id) or {}
    configured = (
        channel.get("origin_binding")
        if isinstance(channel.get("origin_binding"), dict)
        else {}
    )
    return redact_obj({
        **configured,
        "schema_version": str(
            configured.get("schema_version")
            or "channel-origin-binding.v1"
        ),
        "surface": str(configured.get("surface") or "channel"),
        "channel_id": channel_id,
        "thread_id": thread_id,
    })


def _record_failure_or_attention(
    *,
    writer: EventWriter,
    source_event: ZfEvent,
    receipt_id: str,
    idempotency_key: str,
    candidate: dict[str, Any],
    reason: str,
) -> str:
    events = writer.event_log.read_all()
    attempts = [
        event
        for event in events
        if event.type == CHANNEL_RESULT_RECEIPT_FAILED
        and str((event.payload or {}).get("idempotency_key") or "")
        == idempotency_key
    ]
    channel_id = str(candidate.get("channel_id") or "")
    thread_id = str(candidate.get("thread_id") or "main")
    if len(attempts) < _MAX_DELIVERY_ATTEMPTS:
        writer.emit(
            CHANNEL_RESULT_RECEIPT_FAILED,
            actor="zf-channel-result-reconciler",
            task_id=str(candidate.get("task_id") or source_event.task_id or "")
            or None,
            causation_id=source_event.id,
            correlation_id=channel_id or source_event.correlation_id,
            payload={
                "schema_version": CHANNEL_RESULT_RECEIPT_SCHEMA_VERSION,
                "receipt_id": receipt_id,
                "channel_id": channel_id,
                "thread_id": thread_id,
                "source_event_id": source_event.id,
                "source_event_type": source_event.type,
                "idempotency_key": idempotency_key,
                "attempt": len(attempts) + 1,
                "reason": reason,
                "source": "runtime",
            },
        )
        return "failed"
    attention_id = "attn-" + hashlib.sha1(
        f"channel-receipt:{idempotency_key}".encode("utf-8")
    ).hexdigest()[:12]
    if any(
        event.type == "runtime.attention.needed"
        and str((event.payload or {}).get("attention_id") or "")
        == attention_id
        for event in events
    ):
        return "duplicate"
    writer.emit(
        "runtime.attention.needed",
        actor="zf-channel-result-reconciler",
        task_id=str(candidate.get("task_id") or source_event.task_id or "")
        or None,
        causation_id=attempts[-1].id if attempts else source_event.id,
        correlation_id=channel_id or source_event.correlation_id,
        payload={
            "schema_version": "runtime.attention.needed.v0",
            "attention_id": attention_id,
            "fingerprint": f"channel-result-receipt:{idempotency_key}",
            "severity": "warn",
            "source": "channel-result-reconciler",
            "title": "channel.result.receipt.delivery_failed",
            "summary": reason,
            "task_id": str(
                candidate.get("task_id") or source_event.task_id or ""
            ),
            "source_event_ids": [source_event.id],
            "source_ref": f"channel-receipt:{receipt_id}",
            "suggested_route": "run_manager",
            "suggested_action": {},
        },
    )
    return "attention"


def _recorded_event(
    events: list[ZfEvent],
    idempotency_key: str,
) -> ZfEvent | None:
    return next(
        (
            event
            for event in reversed(events)
            if event.type == CHANNEL_RESULT_RECEIPT_RECORDED
            and str((event.payload or {}).get("idempotency_key") or "")
            == idempotency_key
        ),
        None,
    )


def _write_cursor(
    state_dir: Path,
    *,
    events: list[ZfEvent],
    considered: int,
    recorded: int,
    failed: int,
    attention: int,
) -> None:
    path = state_dir / "projections" / "channel-result-receipts" / "cursor.json"
    payload = {
        "schema_version": _RECONCILE_CURSOR_SCHEMA_VERSION,
        "last_event_id": events[-1].id if events else "",
        "event_count": len(events),
        "considered": considered,
        "recorded": recorded,
        "failed": failed,
        "attention": attention,
    }
    atomic_write_text(
        path,
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
    )


def _receipt_idempotency_key(
    *,
    channel_id: str,
    thread_id: str,
    receipt_kind: str,
    source_digest: str,
    revision: int,
) -> str:
    return "|".join([
        channel_id,
        thread_id,
        receipt_kind,
        source_digest,
        str(revision),
    ])


def _source_status(source: ZfEvent) -> str:
    if source.type == "task.created":
        return "created"
    if source.type == "run.goal.blocked":
        return "blocked"
    if source.type.startswith("ship."):
        return "delivered"
    return "completed"


def _event_digest(event: ZfEvent) -> str:
    body = {
        "id": event.id,
        "type": event.type,
        "task_id": event.task_id,
        "correlation_id": event.correlation_id,
        "payload": event.payload,
    }
    encoded = json.dumps(
        body,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _first_text(payload: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = str(payload.get(key) or "").strip()
        if value:
            return value
    return ""


def _safe_segment(value: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "-", value).strip(".-")
    return safe or "unknown"


__all__ = [
    "CHANNEL_RESULT_RECEIPT_FAILED",
    "CHANNEL_RESULT_RECEIPT_RECORDED",
    "CHANNEL_RESULT_RECEIPT_SCHEMA_VERSION",
    "ChannelReceiptReconcileResult",
    "reconcile_channel_result_receipts",
    "record_channel_result_receipt",
]
