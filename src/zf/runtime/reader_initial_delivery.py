"""Reconcile a pending Reader with the transport claim left by the previous watcher."""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

from zf.core.events import ZfEvent
from zf.core.security.hash import sha256_file
from zf.runtime.task_attempt_runtime import task_attempt_store
from zf.runtime.transport import DispatchContext


if TYPE_CHECKING:
    from zf.core.config.schema import RoleConfig
    from zf.runtime.call_result_runtime import PreparedCallOperation
    from zf.runtime.fanout import FanoutChild, FanoutContext
    from zf.runtime.orchestrator import Orchestrator


@dataclass(frozen=True)
class ReaderDelivery:
    context: DispatchContext | None = None
    receipt: str = ""


def prior_reader_delivery(
    runtime: Orchestrator,
    context: FanoutContext,
    child: FanoutChild,
    role: RoleConfig,
    call: PreparedCallOperation | None,
    dispatch_id: str,
    causation_id: str,
) -> ReaderDelivery | None:
    if call is None or not call.task_id or call.ensure_status not in {"requested", "running"}:
        return None
    row = task_attempt_store(runtime).current_for_task(call.task_id, dispatch_id=dispatch_id)
    if not row or not row.get("delivery_claimed_at") or row.get("status") == "prepared":
        return None
    if (row["run_id"], row["operation_id"], row["instance_id"]) != (
        call.workflow_run_id,
        call.operation_id,
        role.instance_id,
    ):
        return None
    events = runtime.event_log.read_all()
    receipt = transport_receipt(row, events)
    if receipt is not None:
        transport = runtime._dispatch_context(
            role=role,
            briefing_path=Path(row["briefing_ref"]),
            task_id=call.task_id,
            parent_task_id=row.get("parent_task_id") or None,
            trace_id=context.trace_id,
            run_id=call.workflow_run_id,
            operation_id=call.operation_id,
            dispatch_id=dispatch_id,
        )
        return ReaderDelivery(
            replace(transport, attempt_id=row["attempt_id"], lease_id=row["lease_id"]), receipt.id
        )
    record_wait(
        runtime,
        row,
        events,
        fanout_id=context.fanout_id,
        stage_id=context.stage_id,
        child_id=child.child_id,
        request_hash=call.request_hash,
        causation_id=causation_id,
    )
    return ReaderDelivery()


def transport_receipt(row: dict[str, Any], events: list[ZfEvent]) -> ZfEvent | None:
    if not row.get("sent_at") or row["status"] not in {"sent", "expired"}:
        return None
    expected = {
        "workflow_run_id": row["run_id"],
        "operation_id": row["operation_id"],
        "task_id": row["task_id"],
        "dispatch_id": row["dispatch_id"],
        "role_instance": row["instance_id"],
        "attempt_id": row["attempt_id"],
        "lease_id": row["lease_id"],
    }
    for event in reversed(events):
        if (
            event.type == "task.attempt.sent"
            and event.origin == "kernel"
            and event.actor == "orchestrator"
            and all(event.payload.get(key) == value for key, value in expected.items())
        ):
            try:
                if event.payload.get("briefing_digest") == sha256_file(Path(row["briefing_ref"])):
                    return event
            except OSError:
                pass
    return None


def record_wait(
    runtime: Orchestrator,
    row: dict[str, Any],
    events: list[ZfEvent],
    *,
    fanout_id: str,
    stage_id: str,
    child_id: str,
    request_hash: str,
    causation_id: str,
) -> ZfEvent:
    prior = next(
        (
            e
            for e in events
            if e.type == "fanout.child.dispatch_deferred"
            and e.origin == "kernel"
            and e.payload.get("reason") == "reader_delivery_unconfirmed"
            and e.payload.get("task_attempt_id") == row["attempt_id"]
        ),
        None,
    )
    if prior is not None:
        return prior
    return runtime.event_writer.append(
        ZfEvent(
            type="fanout.child.dispatch_deferred",
            actor="zf-cli",
            task_id=row["task_id"],
            correlation_id=row["run_id"],
            causation_id=causation_id,
            payload={
                "workflow_run_id": row["run_id"],
                "task_id": row["task_id"],
                "stage_id": stage_id,
                "trace_id": row["run_id"],
                "fanout_id": fanout_id,
                "child_id": child_id,
                "role_instance": row["instance_id"],
                "dispatch_id": row["dispatch_id"],
                "run_id": row["dispatch_id"],
                "operation_id": row["operation_id"],
                "task_attempt_id": row["attempt_id"],
                "lease_id": row["lease_id"],
                "request_hash": request_hash,
                "reason": "reader_delivery_unconfirmed",
                "delivery_status": "unknown",
                "recovery_owner": "run_manager",
                "dispatchable": False,
            },
        )
    )


def pending_initial_obligation(row: dict[str, Any], events: list[ZfEvent]) -> dict[str, str] | None:
    """Bind an already-expired cold start before the ordinary lease retry is proposed."""
    if not row.get("delivery_claimed_at"):
        return None
    requested = next(
        (
            e
            for e in reversed(events)
            if e.type == "workflow.operation.requested"
            and e.origin == "kernel"
            and e.payload.get("operation_id") == row["operation_id"]
        ),
        None,
    )
    if not requested:
        return None
    p = requested.payload
    if (
        p.get("operation_type") != "fanout_reader_child"
        or not p.get("request_hash")
        or p.get("workflow_run_id") != row["run_id"]
        or p.get("role_instance") != row["instance_id"]
        or p.get("task_id") != row["task_id"]
        or p.get("active_attempt_id") != row["dispatch_id"]
    ):
        return None
    closed_fanouts = set()
    for e in reversed(events):
        if e.origin != "kernel":
            continue
        data = e.payload
        if data.get("operation_id") == row["operation_id"] and e.type in {
            "workflow.operation.settled",
            "workflow.operation.failed",
            "workflow.operation.cancelled",
            "workflow.operation.superseded",
            "workflow.operation.blocked",
        }:
            return None
        if e.type in {"fanout.cancelled", "fanout.timed_out", "fanout.aggregate.completed"}:
            closed_fanouts.add(data.get("fanout_id"))
        if (
            e.type in {"fanout.child.dispatched", "fanout.child.completed", "fanout.child.failed"}
            and data.get("run_id") == row["dispatch_id"]
        ):
            return None
        if (
            e.type != "fanout.started"
            or data.get("fanout_id") in closed_fanouts
            or data.get("trace_id") != row["run_id"]
            or data.get("topology") != "fanout_reader"
            or data.get("stage_id") != p.get("parent_stage_id")
        ):
            continue
        for child in data.get("expected_children") or []:
            if (
                child.get("role_instance") == row["instance_id"]
                and f"run-{data['fanout_id']}-{child['child_id']}" == row["dispatch_id"]
            ):
                return {
                    "fanout_id": data["fanout_id"],
                    "stage_id": data["stage_id"],
                    "child_id": child["child_id"],
                    "request_hash": p["request_hash"],
                    "causation_id": e.id,
                }
    return None


def delivery_waits(events: list[ZfEvent]) -> dict[tuple[str, str], ZfEvent]:
    """Index open, attempt-bound transport holds for pending Readers."""
    held = {}
    for event in events:
        p = event.payload
        if event.origin != "kernel" or not p.get("fanout_id"):
            continue
        if event.type in {"fanout.cancelled", "fanout.timed_out", "fanout.aggregate.completed"}:
            held = {key: value for key, value in held.items() if key[0] != p["fanout_id"]}
            continue
        if not p.get("child_id"):
            continue
        key = (p["fanout_id"], p["child_id"])
        if (
            event.type == "fanout.child.dispatch_deferred"
            and p.get("reason") == "reader_delivery_unconfirmed"
            and p.get("task_attempt_id")
            and p.get("lease_id")
        ):
            held[key] = event
        elif event.type in {"fanout.child.dispatched", "fanout.child.completed", "fanout.child.failed"}:
            held.pop(key, None)
    return held


def hold_pending_delivery(runtime: Orchestrator, wait: ZfEvent | None, events: list[ZfEvent]) -> bool:
    """Yield to the old writer until a proven send or a sanctioned release is recorded."""
    if wait is None:
        return False
    p = wait.payload
    row = task_attempt_store(runtime).current_for_task(p["task_id"], dispatch_id=p["dispatch_id"])
    if not row or row["attempt_id"] != p["task_attempt_id"] or row["lease_id"] != p["lease_id"]:
        return False
    if row["status"] == "prepared" or transport_receipt(row, events) is not None:
        return False
    return True


def diagnose_reader_delivery(
    runtime: Orchestrator, source: ZfEvent, events: list[ZfEvent], *, deadline: str
) -> ZfEvent:
    """An expired confirmation window authorizes inspection of the original delivery."""
    p = source.payload
    prior = next(
        (
            e
            for e in events
            if e.origin == "kernel"
            and e.causation_id == source.id
            and e.type in {"fanout.child.delivery_unconfirmed", "human.escalate"}
        ),
        None,
    )
    if prior is not None:
        return prior
    details = {
        **p,
        "source_event_id": source.id,
        "deadline": deadline,
        "failure_class": "reader_transport_confirmation_expired",
        "delivery_status": "unknown",
        "retryable": False,
        "recommendation": "Inspect the original provider and transport receipts for this "
        "operation, dispatch, attempt and lease. Require proof of non-delivery before allowing another send.",
    }
    manager = runtime.config.runtime.run_manager
    managed = manager.resident_agent.enabled or manager.reflect.enabled
    event_type = "fanout.child.delivery_unconfirmed" if managed else "human.escalate"
    if not managed:
        details.update(
            reason="Reader transport delivery needs receipt reconciliation",
            allowed_actions=["operator_review"],
        )
    return runtime.event_writer.append(
        ZfEvent(
            type=event_type,
            actor="zf-cli",
            task_id=source.task_id,
            causation_id=source.id,
            correlation_id=p["workflow_run_id"],
            payload={**details, "recovery_owner": "run_manager" if managed else "operator"},
        )
    )


def expired_reader_claim(
    runtime: Orchestrator, row: dict[str, Any], events: list[ZfEvent], waits: dict[tuple[str, str], ZfEvent]
) -> int | None:
    """Lease expiry is a diagnosis deadline, never evidence that input was not sent."""
    wait = next(
        (
            e
            for e in waits.values()
            if e.payload["task_attempt_id"] == row["attempt_id"]
            and e.payload["lease_id"] == row["lease_id"]
            and e.payload["operation_id"] == row["operation_id"]
        ),
        None,
    )
    if wait is None:
        scope = pending_initial_obligation(row, events)
        if scope is None:
            return None
        if transport_receipt(row, events) is not None:
            return 0
        wait = record_wait(runtime, row, events, **scope)
    notice = diagnose_reader_delivery(runtime, wait, events, deadline="attempt_lease")
    if (
        row.get("retryable") is not False
        or row.get("failure_class") != "reader_transport_confirmation_expired"
    ):
        task_attempt_store(runtime).update(
            row["attempt_id"],
            status="expired",
            updated_at=row["updated_at"],
            failure_reason="Reader delivery confirmation window expired",
            failure_class="reader_transport_confirmation_expired",
            retryable=False,
            recovery_owner=notice.payload["recovery_owner"],
            terminal_event_id=notice.id,
        )
        return 1
    return 0
