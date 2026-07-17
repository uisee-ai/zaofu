"""Owner-visible message delivery receipts.

Supervisor emits ``owner.visible_message.requested`` when a human should be
notified.  This module is the deterministic delivery sidecar that turns those
requests into Feishu delivery attempts and receipt events.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from zf.core.events import EventWriter, ZfEvent
from zf.core.events.log import EventLog
from zf.core.security.redaction import redact_obj
from zf.integrations.feishu.projection import RoutingConfig
from zf.integrations.feishu.transport import FeishuMessage, FeishuTransport
from zf.runtime.owner_visible_flip import flip_acknowledged_owner_cards_once
from zf.runtime.owner_visible_render import (
    humanize_owner_title,
    owner_message_dedup_key,
    owner_message_is_empty,
)

# Cross-pass content-fold window: an identical-looking owner card delivered
# within this window is suppressed as a duplicate instead of re-paging the
# owner every tick. Long enough to absorb tick-to-tick repeats, short enough
# that a genuinely persisting problem re-pages within the hour.
_CONTENT_DEDUP_WINDOW_S = 1800.0


def _recent_delivered_content_keys(
    events: list[ZfEvent], *, target: str,
) -> set[str]:
    """Dedup keys of cards delivered to ``target`` within the fold window.

    Reads the persisted ``dedup_key`` off delivered receipts (receipts written
    before that field existed simply never fold — safe, backward compatible).
    """
    now = datetime.now(timezone.utc)
    keys: set[str] = set()
    for event in events:
        if event.type != OWNER_MESSAGE_DELIVERED:
            continue
        payload = event.payload if isinstance(event.payload, dict) else {}
        if str(payload.get("target") or "") != target:
            continue
        key = str(payload.get("dedup_key") or "")
        if not key:
            continue
        try:
            ts = datetime.fromisoformat(str(event.ts))
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        if (now - ts).total_seconds() <= _CONTENT_DEDUP_WINDOW_S:
            keys.add(key)
    return keys


OWNER_MESSAGE_REQUESTED = "owner.visible_message.requested"
OWNER_MESSAGE_ATTEMPTED = "owner.visible_message.delivery_attempted"
OWNER_MESSAGE_DELIVERED = "owner.visible_message.delivered"
OWNER_MESSAGE_FAILED = "owner.visible_message.failed"
OWNER_MESSAGE_ROUTE_UNHEALTHY = "owner.visible_message.route_unhealthy"
OWNER_MESSAGE_SUPPRESSED = "owner.visible_message.suppressed"
GENERIC_APPROVAL_REQUESTED = "approval.requested"
OWNER_MESSAGE_TERMINAL = {
    OWNER_MESSAGE_DELIVERED,
    "owner.visible_message.expired",
    "owner.visible_message.superseded",
    OWNER_MESSAGE_SUPPRESSED,
}


@dataclass(frozen=True)
class OwnerVisibleDeliveryResult:
    ok: bool
    status: str
    considered: int = 0
    attempted: int = 0
    delivered: int = 0
    failed: int = 0
    skipped: int = 0
    attempted_event_ids: list[str] | None = None
    delivered_event_ids: list[str] | None = None
    failed_event_ids: list[str] | None = None


def _emit_owner_hygiene_suppressed(
    writer: EventWriter,
    event: ZfEvent,
    payload: dict[str, Any],
    *,
    reason: str,
    extra: dict[str, Any] | None = None,
) -> None:
    """Suppress an owner-visible request for a delivery-hygiene reason (empty
    body / duplicate fingerprint) — backlog 2026-07-07-1315. Compact receipt: no
    route resolution needed, we are dropping before any transport work."""
    writer.emit(
        OWNER_MESSAGE_SUPPRESSED,
        actor="zf-owner-visible-delivery",
        task_id=event.task_id or _text(payload, "task_id") or None,
        causation_id=event.id,
        correlation_id=event.correlation_id,
        payload=redact_obj({
            "message_id": _message_id(payload, fallback=event.id),
            "status": "suppressed",
            "reason": reason,
            "fingerprint": _text(payload, "fingerprint"),
            **(extra or {}),
        }),
    )


def deliver_owner_visible_messages_once(
    *,
    event_log: EventLog,
    writer: EventWriter,
    transport: FeishuTransport,
    routing: RoutingConfig,
    target: str = "feishu",
    max_attempts: int = 1,
) -> OwnerVisibleDeliveryResult:
    """Deliver pending owner-visible messages for one target.

    The function is idempotent over ``message_id`` + ``target``. Delivered,
    expired, and superseded messages are not retried. Failed messages can be
    retried only when ``max_attempts`` is raised by the caller.
    """

    target = str(target or "feishu").strip() or "feishu"
    max_attempts = max(int(max_attempts or 1), 1)
    events = event_log.read_all()
    lifecycle = _delivery_lifecycle(events, target=target)
    unhealthy_routes = _unhealthy_owner_visible_routes(events, target=target)
    requested = [event for event in events if event.type == OWNER_MESSAGE_REQUESTED]
    runtime_stopped = _runtime_state_from_event_log(event_log) == "stopped"
    attempted_event_ids: list[str] = []
    delivered_event_ids: list[str] = []
    failed_event_ids: list[str] = []
    skipped = 0
    # backlog 2026-07-07-1315: collapse duplicate content within one pass so a
    # repeating signal (recycle_threshold_exceeded ×9, each with a DISTINCT
    # fingerprint) is not spammed to the owner. Keyed on rendered content, not
    # fingerprint, precisely because the spam carries varying fingerprints.
    # 2026-07-17: seed the fold set from recent delivered receipts so the fold
    # also holds ACROSS passes — the in-pass set alone let the same card ship
    # once per tick (/tmp/runm.png: identical "completion claims" card twice).
    delivered_content_keys: set[str] = _recent_delivered_content_keys(
        events, target=target,
    )
    for event in requested:
        payload = event.payload if isinstance(event.payload, dict) else {}
        if not _wants_target(payload, target):
            skipped += 1
            continue
        if runtime_stopped and not _stopped_runtime_delivery_allowed(payload):
            _emit_owner_hygiene_suppressed(
                writer, event, payload, reason="runtime_stopped",
                extra={"runtime_state": "stopped", "target": target})
            skipped += 1
            continue
        # Drop empty (no title/summary) requests: the old path shipped a
        # near-empty field dump for these (backlog 2026-07-07-1315).
        if owner_message_is_empty(payload):
            _emit_owner_hygiene_suppressed(
                writer, event, payload, reason="empty_owner_message")
            skipped += 1
            continue
        content_key = owner_message_dedup_key(payload)
        if content_key in delivered_content_keys:
            _emit_owner_hygiene_suppressed(
                writer, event, payload, reason="duplicate_owner_message",
                extra={"dedup_key": content_key})
            skipped += 1
            continue
        message_id = _message_id(payload, fallback=event.id)
        state = lifecycle.get(message_id, {"attempts": 0, "terminal": False})
        if bool(state.get("terminal")) or int(state.get("attempts") or 0) >= max_attempts:
            skipped += 1
            continue
        if _should_suppress_delivery(payload, target=target):
            route = _routing_role(payload, routing)
            receive_id = routing.channels.get(route, "")
            receive_id_type = routing.receive_id_type_for(route)
            suppressed = writer.emit(
                OWNER_MESSAGE_SUPPRESSED,
                actor="zf-owner-visible-delivery",
                task_id=event.task_id or _text(payload, "task_id") or None,
                causation_id=event.id,
                correlation_id=event.correlation_id,
                payload=redact_obj({
                    **_receipt_payload(
                        event=event,
                        payload=payload,
                        target=target,
                        route=route,
                        receive_id=receive_id,
                        receive_id_type=receive_id_type,
                        delivery_id=_delivery_id(message_id, target, 0),
                        attempt=0,
                    ),
                    "status": "suppressed",
                    "reason": "non_human_supervisor_message",
                    "human_action_required": False,
                }),
            )
            skipped += 1
            continue
        attempt_no = int(state.get("attempts") or 0) + 1
        route = _routing_role(payload, routing)
        receive_id = routing.channels.get(route, "")
        receive_id_type = routing.receive_id_type_for(route)
        route_key = _delivery_route_key(
            target=target,
            route=route,
            receive_id=receive_id,
            receive_id_type=receive_id_type,
        )
        if route_key in unhealthy_routes:
            skipped += 1
            continue
        delivery_id = _delivery_id(message_id, target, attempt_no)
        base = _receipt_payload(
            event=event,
            payload=payload,
            target=target,
            route=route,
            receive_id=receive_id,
            receive_id_type=receive_id_type,
            delivery_id=delivery_id,
            attempt=attempt_no,
        )
        attempted = writer.emit(
            OWNER_MESSAGE_ATTEMPTED,
            actor="zf-supervisor",
            task_id=event.task_id or _text(payload, "task_id") or None,
            causation_id=event.id,
            correlation_id=event.correlation_id,
            payload=redact_obj({**base, "status": "delivery_attempted"}),
        )
        attempted_event_ids.append(attempted.id)
        if not receive_id:
            failed = _emit_failed(
                writer,
                attempted=attempted,
                event=event,
                payload=payload,
                base=base,
                reason=f"{target} delivery route {route!r} is not configured",
            )
            failed_event_ids.append(failed.id)
            continue
        preflight_failure = _receive_id_preflight_failure(
            target=target,
            receive_id=receive_id,
            receive_id_type=receive_id_type,
        )
        if preflight_failure:
            failed = _emit_failed(
                writer,
                attempted=attempted,
                event=event,
                payload=payload,
                base=base,
                reason=preflight_failure,
            )
            failed_event_ids.append(failed.id)
            continue
        sent_feishu_mid = ""
        try:
            body = _format_owner_message(event, payload)
            # Interactive card first (severity-colored header + lark_md body,
            # same visual family as the run-manager status card); if the card
            # path throws, fall back to the plain-text message — a paged alert
            # must never be lost to a rendering problem.
            try:
                # ack-flip: keep the provider message_id so a later
                # runtime.attention.acknowledged can update THIS card in place
                # (text fallback has no card to flip — mid stays empty).
                sent_feishu_mid = str(transport.send_card(FeishuMessage(
                    chat_id=receive_id,
                    receive_id_type=receive_id_type,
                    msg_type="interactive",
                    content=json.dumps(
                        _owner_visible_card(payload, body), ensure_ascii=False,
                    ),
                )) or "")
            except Exception:
                transport.send_message(FeishuMessage(
                    chat_id=receive_id,
                    receive_id_type=receive_id_type,
                    content=body,
                ))
        except Exception as exc:
            failed = _emit_failed(
                writer,
                attempted=attempted,
                event=event,
                payload=payload,
                base=base,
                reason=str(exc),
            )
            _emit_route_unhealthy_if_needed(
                writer,
                failed=failed,
                attempted=attempted,
                event=event,
                base=base,
            )
            failed_event_ids.append(failed.id)
            continue
        delivered = writer.emit(
            OWNER_MESSAGE_DELIVERED,
            actor="zf-supervisor",
            task_id=event.task_id or _text(payload, "task_id") or None,
            causation_id=attempted.id,
            correlation_id=event.correlation_id,
            payload=redact_obj({
                **base,
                "status": "delivered",
                "feishu_message_id": sent_feishu_mid,
            }),
        )
        delivered_event_ids.append(delivered.id)
        delivered_content_keys.add(content_key)

    # ack-flip (task 2026-07-17-0731): flip already-acknowledged cards in the
    # same sidecar pass. Uses the pre-pass event snapshot — cards delivered in
    # THIS pass cannot have been acknowledged yet, they flip on a later pass.
    try:
        flip_acknowledged_owner_cards_once(
            event_log=event_log, transport=transport, target=target,
            events=events,
        )
    except Exception:
        pass  # flipping is cosmetic; it must never break delivery

    failed_count = len(failed_event_ids)
    delivered_count = len(delivered_event_ids)
    return OwnerVisibleDeliveryResult(
        ok=failed_count == 0,
        status="completed" if failed_count == 0 else "failed",
        considered=len(requested),
        attempted=len(attempted_event_ids),
        delivered=delivered_count,
        failed=failed_count,
        skipped=skipped,
        attempted_event_ids=attempted_event_ids,
        delivered_event_ids=delivered_event_ids,
        failed_event_ids=failed_event_ids,
    )


def project_owner_visible_inbox(
    state_dir: Path | None = None,
    *,
    events: list[ZfEvent] | None = None,
) -> dict[str, Any]:
    """Build a read-only inbox projection for owner-visible messages."""
    if events is None:
        if state_dir is None:
            events = []
        else:
            try:
                events = EventLog(Path(state_dir) / "events.jsonl").read_all()
            except Exception:
                events = []
    messages: dict[str, dict[str, Any]] = {}
    for event in events:
        if event.type not in {
            OWNER_MESSAGE_REQUESTED,
            OWNER_MESSAGE_ATTEMPTED,
            OWNER_MESSAGE_DELIVERED,
            OWNER_MESSAGE_FAILED,
            OWNER_MESSAGE_ROUTE_UNHEALTHY,
            OWNER_MESSAGE_SUPPRESSED,
            "owner.visible_message.expired",
            "owner.visible_message.superseded",
        }:
            continue
        payload = event.payload if isinstance(event.payload, dict) else {}
        message_id = _message_id(payload, fallback=event.id)
        row = messages.setdefault(message_id, {
            "message_id": message_id,
            "status": "unknown",
            "task_id": event.task_id or _text(payload, "task_id"),
            "decision_id": _text(payload, "decision_id"),
            "attention_id": _text(payload, "attention_id"),
            "fingerprint": _text(payload, "fingerprint"),
            "severity": _text(payload, "severity"),
            "title": _text(payload, "title"),
            "summary": _text(payload, "summary"),
            "targets": [],
            "attempts": 0,
            "failures": 0,
            "last_error": "",
            "last_event_id": "",
            "last_event_at": "",
        })
        row["last_event_id"] = event.id
        row["last_event_at"] = event.ts
        for target in _delivery_targets(payload):
            if target not in row["targets"]:
                row["targets"].append(target)
        if event.type == OWNER_MESSAGE_REQUESTED:
            row["status"] = "requested"
            for key in ("decision_id", "attention_id", "fingerprint", "severity", "title", "summary"):
                value = _text(payload, key)
                if value:
                    row[key] = value
        elif event.type == OWNER_MESSAGE_ATTEMPTED:
            row["status"] = "delivery_attempted"
            row["attempts"] = int(row.get("attempts") or 0) + 1
        elif event.type == OWNER_MESSAGE_DELIVERED:
            row["status"] = "delivered"
        elif event.type == OWNER_MESSAGE_FAILED:
            row["status"] = "failed"
            row["failures"] = int(row.get("failures") or 0) + 1
            row["last_error"] = _text(payload, "reason") or _text(payload, "error")
        elif event.type == OWNER_MESSAGE_ROUTE_UNHEALTHY:
            row["last_error"] = _text(payload, "reason") or _text(payload, "error_class")
        elif event.type == OWNER_MESSAGE_SUPPRESSED:
            row["status"] = "suppressed"
            row["last_error"] = _text(payload, "reason")
        elif event.type == "owner.visible_message.expired":
            row["status"] = "expired"
        elif event.type == "owner.visible_message.superseded":
            row["status"] = "superseded"
    rows = sorted(
        messages.values(),
        key=lambda row: str(row.get("last_event_at") or ""),
    )
    pending_statuses = {"requested", "delivery_attempted"}
    failed_statuses = {"failed"}
    delivered_statuses = {"delivered"}
    return {
        "schema_version": "owner.visible_message.inbox.v0",
        "is_derived_projection": True,
        "summary": {
            "total": len(rows),
            "pending": sum(1 for row in rows if row.get("status") in pending_statuses),
            "failed": sum(1 for row in rows if row.get("status") in failed_statuses),
            "delivered": sum(1 for row in rows if row.get("status") in delivered_statuses),
        },
        "pending": [row for row in rows if row.get("status") in pending_statuses][-50:],
        "failed": [row for row in rows if row.get("status") in failed_statuses][-50:],
        "recent": rows[-50:],
    }


def _delivery_lifecycle(events: list[ZfEvent], *, target: str) -> dict[str, dict[str, Any]]:
    state: dict[str, dict[str, Any]] = {}
    for event in events:
        if event.type not in {
            OWNER_MESSAGE_ATTEMPTED,
            OWNER_MESSAGE_DELIVERED,
            OWNER_MESSAGE_FAILED,
            OWNER_MESSAGE_SUPPRESSED,
            "owner.visible_message.expired",
            "owner.visible_message.superseded",
        }:
            continue
        payload = event.payload if isinstance(event.payload, dict) else {}
        if _text(payload, "target") != target:
            continue
        message_id = _message_id(payload, fallback="")
        if not message_id:
            continue
        row = state.setdefault(message_id, {"attempts": 0, "terminal": False})
        if event.type == OWNER_MESSAGE_ATTEMPTED:
            row["attempts"] = max(int(row.get("attempts") or 0), _int(payload.get("attempt")))
        if event.type in OWNER_MESSAGE_TERMINAL:
            row["terminal"] = True
        if event.type == OWNER_MESSAGE_FAILED:
            row["attempts"] = max(int(row.get("attempts") or 0), _int(payload.get("attempt")))
    return state


def _unhealthy_owner_visible_routes(
    events: list[ZfEvent],
    *,
    target: str,
) -> set[str]:
    routes: set[str] = set()
    for event in events:
        if event.type not in {OWNER_MESSAGE_ROUTE_UNHEALTHY, OWNER_MESSAGE_FAILED}:
            continue
        payload = event.payload if isinstance(event.payload, dict) else {}
        if _text(payload, "target") != target:
            continue
        error_class = _text(payload, "error_class")
        if error_class != "feishu_open_id_cross_app":
            continue
        key = _delivery_route_key(
            target=target,
            route=_text(payload, "route"),
            receive_id=_text(payload, "receive_id"),
            receive_id_type=_text(payload, "receive_id_type"),
        )
        if key:
            routes.add(key)
    return routes


def _emit_failed(
    writer: EventWriter,
    *,
    attempted: ZfEvent,
    event: ZfEvent,
    payload: dict[str, Any],
    base: dict[str, Any],
    reason: str,
) -> ZfEvent:
    error_class = _delivery_error_class(reason)
    failed = writer.emit(
        OWNER_MESSAGE_FAILED,
        actor="zf-supervisor",
        task_id=event.task_id or _text(payload, "task_id") or None,
        causation_id=attempted.id,
        correlation_id=event.correlation_id,
        payload=redact_obj({
            **base,
            "status": "failed",
            "reason": reason,
            "error_class": error_class,
            "action_hint": _delivery_action_hint(error_class),
        }),
    )
    if error_class == "feishu_route_unconfigured":
        _emit_route_missing_fallback(
            writer,
            failed=failed,
            event=event,
            payload=payload,
            base=base,
            reason=reason,
        )
    return failed


def _emit_route_missing_fallback(
    writer: EventWriter,
    *,
    failed: ZfEvent,
    event: ZfEvent,
    payload: dict[str, Any],
    base: dict[str, Any],
    reason: str,
) -> None:
    message_id = _message_id(payload, fallback=event.id)
    approval_ref = _text(payload, "approval_ref") or f"owner-visible:{message_id}"
    source = _text(payload, "source").lower()
    writer.emit(
        GENERIC_APPROVAL_REQUESTED,
        actor="zf-owner-visible-delivery",
        task_id=event.task_id or _text(payload, "task_id") or None,
        causation_id=failed.id,
        correlation_id=event.correlation_id,
        payload=redact_obj({
            "schema_version": "approval.requested.v1",
            "approval_ref": approval_ref,
            "source_role": (
                "run_manager"
                if source in {"run-manager", "run_manager"} else "supervisor"
            ),
            "owner_route": "owner_visible_delivery",
            "title": "Owner-visible delivery route is not configured",
            "summary": reason,
            "reason": reason,
            "message_id": message_id,
            "source_event_id": event.id,
            "failed_event_id": failed.id,
            "route": str(base.get("route") or ""),
            "target": str(base.get("target") or ""),
            "receive_id_type": str(base.get("receive_id_type") or ""),
            "approve_action": "configure-owner-visible-route",
            "reject_action": "snooze-owner-visible-route",
            "action_hint": _delivery_action_hint("feishu_route_unconfigured"),
        }),
    )


def _emit_route_unhealthy_if_needed(
    writer: EventWriter,
    *,
    failed: ZfEvent,
    attempted: ZfEvent,
    event: ZfEvent,
    base: dict[str, Any],
) -> ZfEvent | None:
    payload = failed.payload if isinstance(failed.payload, dict) else {}
    error_class = _text(payload, "error_class")
    if error_class != "feishu_open_id_cross_app":
        return None
    return writer.emit(
        OWNER_MESSAGE_ROUTE_UNHEALTHY,
        actor="zf-supervisor",
        task_id=failed.task_id,
        causation_id=failed.id,
        correlation_id=event.correlation_id,
        payload=redact_obj({
            **base,
            "status": "route_unhealthy",
            "reason": _text(payload, "reason"),
            "error_class": error_class,
            "action_hint": _text(payload, "action_hint")
            or _delivery_action_hint(error_class),
            "failed_event_id": failed.id,
            "attempted_event_id": attempted.id,
            "route_health": "unhealthy",
        }),
    )


def _receipt_payload(
    *,
    event: ZfEvent,
    payload: dict[str, Any],
    target: str,
    route: str,
    receive_id: str,
    receive_id_type: str,
    delivery_id: str,
    attempt: int,
) -> dict[str, Any]:
    message_id = _message_id(payload, fallback=event.id)
    return {
        "schema_version": "owner.visible_message.delivery.v0",
        "message_id": message_id,
        "delivery_id": delivery_id,
        "target": target,
        "surface": target,
        "route": route,
        "receive_id": receive_id,
        "receive_id_type": receive_id_type,
        "attempt": attempt,
        "idempotency_key": f"owner-visible:{message_id}:{target}:{attempt}",
        "source_event_id": event.id,
        "decision_id": _text(payload, "decision_id"),
        "attention_id": _text(payload, "attention_id"),
        "fingerprint": _text(payload, "fingerprint"),
        "task_id": event.task_id or _text(payload, "task_id"),
        "severity": _text(payload, "severity"),
        "title": _text(payload, "title"),
        # Content-fold key persisted on every receipt so later passes can
        # window-dedupe against what was actually delivered (the in-pass
        # ``delivered_content_keys`` set dies with the pass; /tmp/runm.png
        # showed the same card shipped twice across consecutive ticks).
        "dedup_key": owner_message_dedup_key(payload),
    }


def _format_owner_message(event: ZfEvent, payload: dict[str, Any]) -> str:
    # backlog 2026-07-07-1315: owner-facing text is now a friendly, Chinese,
    # severity-tagged message (see owner_visible_render) instead of the developer
    # key-value dump + ``/zf`` CLI line this used to ship.
    from zf.runtime.owner_visible_render import render_owner_message

    return render_owner_message(
        payload,
        task_id=event.task_id or _text(payload, "task_id"),
    )


_CARD_HEADER_TEMPLATE = {
    "critical": "red",
    "high": "red",
    "medium": "orange",
    "low": "grey",
}


def _owner_visible_card(payload: dict[str, Any], body: str) -> dict[str, Any]:
    severity = _text(payload, "severity").lower()
    # 2026-07-17 card-quality review: header in plain Chinese where the reason
    # table knows the title; severity stays expressed by the header colour and
    # the internal policy enum never reaches the owner (both used to ride the
    # note line as ``severity=high · policy=owner_on_repair_failed``).
    raw_title = _text(payload, "title")
    title = humanize_owner_title(raw_title) if raw_title else "ZaoFu 告警"
    note_bits = [
        part for part in (
            f"任务 {_text(payload, 'task_id')}" if _text(payload, "task_id") else "",
            f"run {_text(payload, 'run_id')}" if _text(payload, "run_id") else "",
        ) if part
    ]
    elements: list[dict[str, Any]] = [
        {"tag": "div", "text": {"tag": "lark_md", "content": body}},
    ]
    # 2026-07-17 card-quality L3: real buttons instead of the dead
    # "回复「重试」" prompt. Every button maps to an EXISTING backend semantic:
    # acknowledge rides the same card.action.trigger → ingest → gate chain the
    # plan/RM cards use (verb registered in gateway + cli/feishu), details is a
    # zero-backend deep link. No button is rendered without its real target.
    actions: list[dict[str, Any]] = []
    attention_id = _text(payload, "attention_id")
    if attention_id and bool(payload.get("human_action_required")):
        actions.append({
            "tag": "button",
            "text": {"tag": "plain_text", "content": "✅ 确认收到"},
            "type": "primary",
            "value": {"action": f"attention-ack:{attention_id}"},
        })
    web_base_url = os.environ.get("ZF_WEB_BASE_URL", "").strip()
    if web_base_url:
        actions.append({
            "tag": "button",
            "text": {"tag": "plain_text", "content": "🔍 详情(Web 收件箱)"},
            "type": "default",
            "url": f"{web_base_url.rstrip('/')}/?page=inbox",
        })
    if actions:
        elements.append({"tag": "action", "actions": actions})
    if note_bits:
        elements.append({
            "tag": "note",
            "elements": [{"tag": "plain_text", "content": " · ".join(note_bits)}],
        })
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": _CARD_HEADER_TEMPLATE.get(severity, "blue"),
            "title": {
                "tag": "plain_text",
                "content": f"{_owner_message_header(payload)} {title}"[:120],
            },
        },
        "elements": elements,
    }


def _owner_message_header(payload: dict[str, Any]) -> str:
    source = _text(payload, "source").lower()
    handled_by = _text(payload, "handled_by").lower()
    # 找人出口收敛一期:supervisor only signs when the Run Manager is
    # demonstrably unavailable — the 「兜底」 marker IS the anomaly signal.
    if source == "supervisor_fallback":
        return "[ZaoFu Supervisor·兜底]"
    if source in {"watchdog", "run_manager_watchdog"}:
        return "[ZaoFu Watchdog]"
    if source in {"run-manager", "run_manager"} or handled_by in {"run-manager", "run_manager"}:
        return "[ZaoFu Run Manager]"
    if source in {"alert", "system"}:
        return "[ZaoFu Alert]"
    return "[ZaoFu Supervisor]"


# Feishu is the human-attention channel: only alerts a human must ACT on get
# pushed (operator convergence decision 2026-07-11; R12's 286x escalate storm
# and this week's attention noise are the evidence). Everything else stays in
# the Web inbox with a suppressed receipt — downgrade, never drop.
_FEISHU_PUSH_POLICIES = frozenset({"owner_immediate", "owner_on_human_required"})
_FEISHU_PUSH_POLICIES_ENV = "ZF_OWNER_VISIBLE_FEISHU_POLICIES"


def _feishu_push_policies() -> frozenset[str]:
    raw = os.environ.get(_FEISHU_PUSH_POLICIES_ENV, "").strip()
    if not raw:
        return _FEISHU_PUSH_POLICIES
    return frozenset(
        part.strip().lower() for part in raw.split(",") if part.strip()
    )


def _should_suppress_delivery(payload: dict[str, Any], *, target: str) -> bool:
    if target != "feishu":
        return False
    if bool(payload.get("human_action_required")):
        return False
    policy = _text(payload, "notification_policy").lower()
    if policy in _feishu_push_policies():
        return False
    # Policy-less critical alerts still page (fail-open for the one alert
    # that must never drown); everything below waits in the inbox.
    if _text(payload, "severity").lower() == "critical":
        return False
    return True


def _runtime_state_from_event_log(event_log: EventLog) -> str:
    try:
        from zf.core.state.session import SessionStore

        session = SessionStore(Path(event_log.path).parent / "session.yaml").load()
        return str(session.runtime_state or "").strip()
    except Exception:
        return ""


def _stopped_runtime_delivery_allowed(payload: dict[str, Any]) -> bool:
    if bool(payload.get("human_action_required")):
        return True
    source = _text(payload, "source").lower()
    handled_by = _text(payload, "handled_by").lower()
    if source in {"alert", "system"}:
        return True
    if source in {"run-manager", "run_manager"} or handled_by in {
        "run-manager",
        "run_manager",
    }:
        return _text(payload, "route") == "run_manager_human_decision"
    title = _text(payload, "title").lower()
    summary = _text(payload, "summary").lower()
    return "stop" in title or "stopped" in title or "shutdown" in summary


def _wants_target(payload: dict[str, Any], target: str) -> bool:
    targets = payload.get("delivery_targets")
    if not isinstance(targets, list):
        return target == "feishu"
    return target in {str(item) for item in targets}


def _delivery_targets(payload: dict[str, Any]) -> list[str]:
    targets = payload.get("delivery_targets")
    if isinstance(targets, list):
        return [str(item) for item in targets if str(item).strip()]
    target = _text(payload, "target") or _text(payload, "surface")
    return [target] if target else []


def _routing_role(payload: dict[str, Any], routing: RoutingConfig) -> str:
    severity = _text(payload, "severity").lower()
    if severity in {"critical", "high", "warn"} and "approval" in routing.channels:
        return "approval"
    for key in ("owner", "alert", "progress", "approval"):
        if key in routing.channels:
            return key
    if severity in {"critical", "high", "warn"}:
        return "approval"
    return "alert"


def _receive_id_preflight_failure(
    *,
    target: str,
    receive_id: str,
    receive_id_type: str,
) -> str:
    if target != "feishu":
        return ""
    if receive_id.startswith("ou_") and receive_id_type != "open_id":
        return "Feishu receive_id starts with ou_ but receive_id_type is not open_id"
    if receive_id.startswith("oc_") and receive_id_type == "open_id":
        return "Feishu receive_id starts with oc_ but receive_id_type is open_id"
    return ""


def _delivery_error_class(reason: str) -> str:
    text = reason.lower()
    if "route" in text and "not configured" in text:
        return "feishu_route_unconfigured"
    if "receive_id" in text and "receive_id_type" in text:
        return "feishu_receive_id_type_mismatch"
    if "cross app" in text:
        return "feishu_open_id_cross_app"
    if "token" in text or "app_id" in text or "app_secret" in text:
        return "feishu_auth"
    if "feishu http" in text or "feishu api error" in text:
        return "feishu_api_error"
    return "delivery_failed"


def _delivery_action_hint(error_class: str) -> str:
    if error_class == "feishu_route_unconfigured":
        return "configure ZF_OWNER_VISIBLE_CHAT or route-specific owner-visible Feishu target env"
    if error_class == "feishu_receive_id_type_mismatch":
        return "match receive_id_type to the Feishu id prefix: oc_ uses chat_id, ou_ uses open_id"
    if error_class == "feishu_open_id_cross_app":
        return "use an open_id/chat_id visible to the configured Feishu app; prefer chat_id for group alerts"
    if error_class == "feishu_auth":
        return "check FEISHU_APP_ID/FEISHU_APP_SECRET or FEISHU_TENANT_ACCESS_TOKEN"
    if error_class == "feishu_api_error":
        return "inspect the Feishu API error and verify target id, receive_id_type, and bot permissions"
    return "inspect owner.visible_message.failed reason and delivery route"


def _message_id(payload: dict[str, Any], *, fallback: str) -> str:
    return _text(payload, "message_id") or _text(payload, "owner_message_id") or fallback


def _delivery_id(message_id: str, target: str, attempt: int) -> str:
    digest = hashlib.sha1(f"{message_id}|{target}|{attempt}".encode("utf-8")).hexdigest()
    return "odel-" + digest[:12]


def _delivery_route_key(
    *,
    target: str,
    route: str,
    receive_id: str,
    receive_id_type: str,
) -> str:
    if not target or not route:
        return ""
    return "|".join([
        str(target or "").strip(),
        str(route or "").strip(),
        str(receive_id or "").strip(),
        str(receive_id_type or "").strip(),
    ])


def _text(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    return value.strip() if isinstance(value, str) else ""


def _int(value: object) -> int:
    try:
        return max(int(value or 0), 0)
    except (TypeError, ValueError):
        return 0


__all__ = [
    "OWNER_MESSAGE_ATTEMPTED",
    "OWNER_MESSAGE_DELIVERED",
    "OWNER_MESSAGE_FAILED",
    "OWNER_MESSAGE_REQUESTED",
    "OWNER_MESSAGE_ROUTE_UNHEALTHY",
    "OWNER_MESSAGE_SUPPRESSED",
    "OwnerVisibleDeliveryResult",
    "deliver_owner_visible_messages_once",
    "project_owner_visible_inbox",
]
