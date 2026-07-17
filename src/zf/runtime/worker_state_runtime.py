"""Worker lifecycle state projection and task-generation guards."""

from __future__ import annotations

from typing import Any

from zf.core.events.model import ZfEvent
from zf.core.state.role_sessions import RoleSessionRegistry
from zf.runtime.event_window import read_runtime_events
from zf.runtime.housekeeping import apply_worker_state_changed_event


_TASK_GENERATION_GUARDED_STATES = frozenset({
    "idle",
    "awaiting_review",
    "completion_pending",
    "blocked_human",
})
_TASK_GENERATION_RELEASE_STATES = frozenset({"idle", "awaiting_review"})


def _fold_worker_transition(
    states: dict[str, str],
    task_ids: dict[str, str],
    *,
    instance_id: str,
    state: str,
    task_id: str,
    generation_override: bool = False,
) -> bool:
    """Fold one transition without letting an old turn retire a new task."""
    current_task_id = task_ids.get(instance_id, "")
    if (
        current_task_id
        and state in _TASK_GENERATION_GUARDED_STATES
        and task_id != current_task_id
        and not generation_override
    ):
        return False
    states[instance_id] = state
    if task_id and state not in _TASK_GENERATION_RELEASE_STATES:
        task_ids[instance_id] = task_id
    elif state in _TASK_GENERATION_RELEASE_STATES and (
        generation_override or not current_task_id or task_id == current_task_id
    ):
        task_ids.pop(instance_id, None)
    return True


class WorkerStateRuntimeMixin:
    """Persist worker state while preventing stale task completions."""

    def _init_worker_state_tracking(self: Any) -> None:
        self._last_worker_state: dict[str, str] = {}
        self._last_worker_task_id: dict[str, str] = {}
        try:
            events = read_runtime_events(self.event_log, self.state_dir)
        except Exception:
            return
        for event in events:
            if event.type != "worker.state.changed" or not event.actor:
                continue
            payload = event.payload if isinstance(event.payload, dict) else {}
            state = str(event.payload.get("to") or "idle")
            task_id = str(event.task_id or event.payload.get("task_id") or "")
            _fold_worker_transition(
                self._last_worker_state,
                self._last_worker_task_id,
                instance_id=event.actor,
                state=state,
                task_id=task_id,
                generation_override=bool(payload.get("generation_override")),
            )

    def _set_worker_state(
        self: Any,
        instance_id: str,
        new_state: str,
        reason: str = "",
        *,
        task_id: str = "",
        force: bool = False,
    ) -> None:
        """Record a restart-safe worker state transition.

        A busy-to-busy transition is retained when the active task changes.
        That task generation prevents a late completion from an older turn
        from releasing a worker that is already processing a newer task.
        """
        old = self._last_worker_state.get(instance_id, "idle")
        current_task_id = self._last_worker_task_id.get(instance_id, "")
        if (
            current_task_id
            and new_state in _TASK_GENERATION_GUARDED_STATES
            and task_id != current_task_id
            and not force
        ):
            return
        if (
            old == new_state
            and not force
            and (
                not task_id
                or current_task_id == task_id
                or (
                    not current_task_id
                    and new_state in _TASK_GENERATION_RELEASE_STATES
                )
            )
        ):
            return
        _fold_worker_transition(
            self._last_worker_state,
            self._last_worker_task_id,
            instance_id=instance_id,
            state=new_state,
            task_id=task_id,
            generation_override=force,
        )
        try:
            payload = {"from": old, "to": new_state, "reason": reason}
            if task_id:
                payload["task_id"] = task_id
                payload["instance_id"] = instance_id
            if force:
                payload["generation_override"] = True
            emitted = self.event_writer.append(ZfEvent(
                type="worker.state.changed",
                actor=instance_id,
                task_id=task_id or None,
                payload=payload,
            ))
            try:
                registry = RoleSessionRegistry(
                    self.state_dir / "role_sessions.yaml",
                    project_root=str(self.project_root),
                )
                apply_worker_state_changed_event(registry, emitted)
            except Exception:
                pass
        except Exception:
            pass

    def worker_health(self: Any) -> dict[str, str]:
        """Fold worker state events into the current per-instance view."""
        out = {
            role.instance_id: "idle"
            for role in self.config.roles
            if role.name != "orchestrator"
        }
        try:
            events = read_runtime_events(self.event_log, self.state_dir)
        except Exception:
            return out
        task_ids: dict[str, str] = {}
        for event in events:
            if event.type == "worker.state.changed" and event.actor in out:
                payload = event.payload if isinstance(event.payload, dict) else {}
                _fold_worker_transition(
                    out,
                    task_ids,
                    instance_id=event.actor,
                    state=str(payload.get("to") or "idle"),
                    task_id=str(event.task_id or payload.get("task_id") or ""),
                    generation_override=bool(payload.get("generation_override")),
                )
        return out

    def _release_stage_actor_liveness(self: Any, event: ZfEvent) -> None:
        """Release only the worker generation that emitted the stage event."""
        actor = str(event.actor or "").strip()
        if not actor or actor in {"zf-cli", "orchestrator"}:
            return
        current_task_id = str(self._last_worker_task_id.get(actor) or "")
        completed_task_id = str(event.task_id or "")
        if current_task_id and current_task_id != completed_task_id:
            return
        if event.type == "dev.blocked":
            target_state = "blocked_human"
        elif event.type in {"dev.build.done", "arch.proposal.done"}:
            target_state = "awaiting_review"
        else:
            target_state = "idle"
        self._set_worker_state(
            actor,
            target_state,
            reason=f"{event.type} for task {event.task_id}",
            task_id=event.task_id or "",
            force=True,
        )


__all__ = ["WorkerStateRuntimeMixin"]
