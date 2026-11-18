"""Preserve dispatch identity at a proven external gate before input is sent."""
from __future__ import annotations

from typing import Any, TYPE_CHECKING

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
    if not external_gate_reason(error):
        return False
    if prepared is None:
        return True
    from zf.runtime.task_attempt_runtime import (
        TaskAttemptDeliveryClaimedError, task_attempt_store, _now, _lease_expiry,
    )

    released = task_attempt_store(runtime).release_unsent_delivery(
        str(prepared.attempt["attempt_id"]), updated_at=_now(),
        lease_expires_at=_lease_expiry(runtime),
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
    runtime._emit_fanout_dispatch_deferred_once(
        fanout_id=context.fanout_id, trace_id=context.trace_id,
        stage_id=context.stage_id, child_id=child.child_id, run_id=run_id,
        role_instance=role.instance_id, prompt_kind="fanout_child", reason=reason,
        state="waiting_external_gate", alive=bool(getattr(error, "pane_alive", False)),
        dispatchable=False, causation_id=causation_id,
    )
    return True
