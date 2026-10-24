"""State-dir scoped serialization for provider-session respawns."""

from __future__ import annotations

from typing import Any

from zf.core.config.schema import RoleConfig
from zf.core.events.model import ZfEvent
from zf.runtime.workflow_runtime_types import WorkflowRuntimeDecision
from zf.runtime.session_mutex import SessionLock, SessionLockBusy


def quiesce_worker_for_replacement(runtime: Any, role: RoleConfig) -> None:
    """Require transport-confirmed absence before replacing a provider session."""
    try:
        runtime.transport.terminate(role.instance_id)
    except Exception as exc:
        # Some transports report the last turn's health as liveness. A failed
        # turn is not proof that cancellation joined its provider thread.
        raise RuntimeError(f"cannot terminate previous worker {role.instance_id}") from exc
    if runtime.transport.is_alive(role.instance_id) is not False:
        raise RuntimeError(f"previous worker {role.instance_id} is not confirmed stopped")


def reset_respawn_success_circuit(runtime: Any, role: RoleConfig) -> None:
    runtime._respawn_success_circuit_opened.discard(role.instance_id)
    current_state = runtime._last_worker_state.get(role.instance_id, "idle")
    runtime._set_worker_state(
        role.instance_id,
        "idle" if current_state == "blocked_human" else current_state,
        reason="operator reset respawn success circuit",
        force=True,
        extra_payload={
            "operator_authorized": True,
            "respawn_success_circuit_reset": True,
        },
    )


def respawn_instance_with_lock(
    runtime: Any,
    role: RoleConfig,
    *,
    recovery_reason: str,
    inject_idle_prompt: bool,
    force_fresh_session: bool = False,
) -> WorkflowRuntimeDecision:
    active_task = runtime._active_task_for_instance(role.instance_id)
    lock_dir = runtime.state_dir / "locks" / "respawns"
    try:
        with SessionLock(lock_dir, role.instance_id):
            return runtime._respawn_instance_with_lease(
                role,
                recovery_reason=recovery_reason,
                inject_idle_prompt=inject_idle_prompt,
                force_fresh_session=force_fresh_session,
            )
    except SessionLockBusy:
        try:
            runtime.event_writer.append(ZfEvent(
                type="worker.respawn.deferred",
                actor=role.instance_id,
                task_id=active_task.id if active_task is not None else None,
                payload={
                    "role": role.name,
                    "instance_id": role.instance_id,
                    "reason": "recovery_lease_held",
                    "requested_by": recovery_reason,
                },
            ))
        except Exception:
            pass
        return WorkflowRuntimeDecision(
            action="respawn_in_progress",
            role=role.instance_id,
            task_id=active_task.id if active_task is not None else "",
            reason="worker respawn already in progress for this role",
        )
