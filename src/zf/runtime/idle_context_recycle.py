"""Release completed on-demand providers when recycling their context."""

from __future__ import annotations

from typing import TYPE_CHECKING

from zf.core.events.model import ZfEvent

if TYPE_CHECKING:
    from zf.core.config.schema import RoleConfig
    from zf.runtime.orchestrator import Orchestrator


def hibernate_idle_context_recycle(
    runtime: Orchestrator, role: RoleConfig,
) -> bool:
    """Rotate a safely suspended idle role; its next dispatch launches fresh."""
    if not runtime._role_is_on_demand(role):
        return False
    if role.name in {"orchestrator", "run-manager"}:
        return False
    instance_id = role.instance_id
    if runtime._active_task_for_instance(instance_id) is not None:
        return False
    if runtime._active_fanout_child_for_instance(instance_id) is not None:
        return False
    now = runtime._now()
    # A context recycle already requires process replacement. Ordinary idle
    # age adds no protection here; every ownership/evidence/safety check does.
    eligible, _reason, _details = runtime._role_suspend_admission(
        role, now=now, require_idle_age=False,
    )
    if not eligible:
        return False
    runtime._suspend_role(role, now=now)
    if runtime._role_lifecycle_meta(role).get("lifecycle_state") != "suspended":
        raise RuntimeError(f"failed to suspend {instance_id} during context recycle")
    replacement = runtime._replace_provider_session_for_recycle(
        role, reason="context_threshold_recycle",
    )
    runtime.event_writer.append(ZfEvent(
        type="worker.recycled",
        actor=instance_id,
        payload={**replacement, "provider_start_deferred": True},
    ))
    runtime._instance_state[instance_id] = "healthy"
    return True
