"""Preserve dispatch identity at a proven external gate before input is sent."""
from __future__ import annotations

from typing import Any, TYPE_CHECKING

from zf.core.events import ZfEvent
from zf.core.state.locks import locked_path
from zf.runtime.tmux import TmuxError

if TYPE_CHECKING:
    from zf.core.config.schema import RoleConfig
    from zf.runtime.task_attempt_runtime import PreparedTaskAttempt


def external_gate_reason(error: BaseException) -> str:
    marker = getattr(error, "interactive_prompt_marker", "")
    if (isinstance(error, TmuxError) and marker
            and getattr(error, "input_not_sent", False) is True):
        return f"provider_interactive:{marker}"
    return ""


def release_external_gate_delivery(
    runtime: Any, prepared: PreparedTaskAttempt | None, error: BaseException,
) -> bool:
    """An unknown/sent/superseded delivery may never be rewound."""
    reason = external_gate_reason(error)
    if not reason:
        return False
    if prepared is None:
        return True
    from zf.runtime.task_attempt_runtime import (
        TaskAttemptDeliveryClaimedError, task_attempt_store, _now, _lease_expiry,
    )

    from zf.runtime.workflow_operation import load_workflow_operation

    row = prepared.attempt
    operation = load_workflow_operation(runtime.event_log, row["operation_id"]) or {}
    reader_wait = (operation.get("operation_type") == "fanout_reader_child"
                   and operation.get("workflow_run_id") == row["run_id"]
                   and operation.get("role_instance") == row["instance_id"])
    released = task_attempt_store(runtime).release_unsent_delivery(
        str(prepared.attempt["attempt_id"]), updated_at=_now(),
        lease_expires_at=_lease_expiry(runtime), gate_reason=reason if reader_wait else "",
    )
    if not released:
        raise TaskAttemptDeliveryClaimedError(
            "external gate cannot release a noncurrent or settled delivery claim"
        ) from error
    return True


def defer_reader_external_gate(runtime: Any, error: BaseException, *, context: Any,
                               child: Any, role: RoleConfig, run_id: str,
                               causation_id: str) -> bool:
    reason = external_gate_reason(error)
    if not reason:
        return False
    record_reader_gate(runtime, {
        "fanout_id": context.fanout_id, "trace_id": context.trace_id, "stage_id": context.stage_id,
        "child_id": child.child_id, "run_id": run_id, "role_instance": role.instance_id,
        "prompt_kind": "fanout_child", "reason": reason, "worker_state": "waiting_external_gate",
        "transport_alive": bool(getattr(error, "pane_alive", False)), "dispatchable": False,
    }, causation_id)
    return True


def latest_reader_gate(events: list[ZfEvent], *, fanout_id: str, child_id: str, dispatch_id: str) -> ZfEvent | None:
    """A different outcome for this exact transport closes the previous wait."""
    for event in reversed(events):
        p = event.payload
        if (event.origin == "kernel" and event.actor == "zf-cli"
                and p.get("fanout_id") == fanout_id and p.get("child_id") == child_id
                and p.get("run_id") == dispatch_id and event.type in {
                    "fanout.child.dispatch_deferred", "fanout.child.dispatched",
                    "fanout.child.completed", "fanout.child.failed"}):
            if event.type == "fanout.child.dispatch_deferred" and str(p.get("reason")).startswith("provider_interactive:"):
                return event
            return None
    return None


def record_reader_gate(runtime, payload: dict, causation_id: str) -> None:
    with locked_path(runtime.state_dir / "locks" / "reader-external-gate"):
        previous = latest_reader_gate(runtime.event_log.read_all(), fanout_id=payload["fanout_id"],
                                      child_id=payload["child_id"], dispatch_id=payload["run_id"])
        if (previous and previous.causation_id == causation_id
                and previous.payload.get("role_instance") == payload["role_instance"]
                and previous.payload.get("reason") == payload["reason"]):
            return
        runtime.event_writer.append(ZfEvent(type="fanout.child.dispatch_deferred", actor="zf-cli",
            task_id=payload.get("task_id"), payload={**payload, "input_not_sent": True,
                "worker_state": "waiting_external_gate", "dispatchable": False,
                "resume_condition": "provider_readiness_and_current_dispatch_admission"},
            causation_id=causation_id,
            correlation_id=payload["trace_id"]))


def reader_gate_still_visible(runtime, events: list[ZfEvent], dispatch: ZfEvent, attempt: int) -> bool:
    from zf.runtime.provider_interactive_prompt import live_provider_interactive_prompt_marker

    p = dispatch.payload
    fanout, child, role = p["fanout_id"], p["child_id"], p["role_instance"]
    wait = latest_reader_gate(events, fanout_id=fanout, child_id=child,
                              dispatch_id=f"run-{fanout}-{child}-retry-{attempt}")
    if not wait or wait.causation_id != dispatch.id or wait.payload.get("role_instance") != role:
        return False
    # This per-role cache only throttles read-only probes. A new watcher may
    # immediately recheck; expiry itself never authorizes a transport send.
    probes = getattr(runtime, "_reader_gate_probes", None)
    if probes is None:
        probes = runtime._reader_gate_probes = {}
    now = runtime._now()
    previous_id, next_probe = probes.get(role, ("", 0.0))
    if previous_id == wait.id and now < next_probe:
        return True
    marker = live_provider_interactive_prompt_marker(runtime.transport, role)
    if marker and wait.payload.get("reason") == f"provider_interactive:{marker}":
        interval = max(1.0, runtime.config.orchestrator.rate_limit_cooldown_s)
        probes[role] = (wait.id, now + interval)
        return True
    probes.pop(role, None)
    return False
