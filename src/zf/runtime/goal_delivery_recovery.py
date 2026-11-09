"""Recover the delivery ledger boundary without repeating unknown effects."""

from __future__ import annotations

from typing import Any

from zf.core.events.model import ZfEvent


_TERMINALS = {"run.delivery.settled", "run.delivery.failed", "run.delivery.blocked"}
_SHIP_TERMINALS = {"ship.completed", "ship.failed", "ship.blocked", "ship.conflict"}


def pending_delivery_request(events: list[ZfEvent], claim_id: str) -> ZfEvent | None:
    latest = next((
        event for event in reversed(events)
        if event.type in _TERMINALS | {"run.delivery.requested"}
        and (event.payload or {}).get("claim_id") == claim_id
    ), None)
    return latest if latest and latest.type == "run.delivery.requested" else None


def recover_delivery_receipt(runtime: Any, request: ZfEvent) -> bool:
    """Return True when a receipt or uncertain effect forbids a fresh ship.

    Caller holds the delivery lock. ShipService emits ship.started before its
    first git mutation, so an absent started/terminal event is safe to redrive.
    A started operation without a receipt needs the recovery owner's decision.
    """
    events = runtime.event_log.read_all()
    body = request.payload or {}
    if any(
        event.type in _TERMINALS
        and (event.payload or {}).get("delivery_operation_id") == body.get("delivery_operation_id")
        and (event.payload or {}).get("claim_id") == body.get("claim_id")
        for event in events
    ):
        return True
    related = [event for event in events if event.causation_id == request.id]
    terminal = next((e for e in reversed(related) if e.type in _SHIP_TERMINALS), None)
    started = any(e.type == "ship.started" for e in related)
    if terminal is None and not started:
        return False
    matched = bool(
        terminal and terminal.type == "ship.completed"
        and body.get("target_commit")
        and (terminal.payload or {}).get("source_commit") == body["target_commit"]
        and (terminal.payload or {}).get("final_commit")
    )
    failed = bool(terminal and terminal.type != "ship.completed")
    runtime.event_writer.append(ZfEvent(
        type="run.delivery.settled" if matched else "run.delivery.failed" if failed else "run.delivery.blocked",
        actor="zf-cli",
        origin="kernel",
        causation_id=request.id,
        correlation_id=request.correlation_id,
        payload={
            **body,
            "ship_event_id": terminal.id if terminal else "",
            "ship_event_type": terminal.type if terminal else "",
            "ship_result": dict(terminal.payload or {}) if terminal else {},
            "recovery_owner": "run_manager",
            "effect_state": "confirmed" if matched or failed else "unknown",
            "reason": "recovered delivery receipt" if matched or failed else "delivery effect unknown; reconciliation required before retry",
        },
    ))
    return True
