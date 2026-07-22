"""Shared availability and liveness fence for fanout child dispatch."""

from __future__ import annotations

import time

from zf.core.config.schema import RoleConfig
from zf.core.events.model import ZfEvent


class FanoutDispatchLivenessMixin:
    def _fanout_dispatch_deferred_recently(
        self,
        *,
        fanout_id: str,
        child_id: str,
        role_instance: str,
        reason: str = "",
        window_s: float = 60.0,
    ) -> bool:
        try:
            events = self.event_log.read_all()
        except Exception:
            return False
        now = self._now()
        for event in reversed(events):
            if event.type != "fanout.child.dispatch_deferred":
                continue
            payload = event.payload if isinstance(event.payload, dict) else {}
            if (
                str(payload.get("fanout_id") or "") != fanout_id
                or str(payload.get("child_id") or "") != child_id
                or str(payload.get("role_instance") or "") != role_instance
            ):
                continue
            if reason and str(payload.get("reason") or "") != reason:
                continue
            try:
                return now - self._event_epoch(event) < window_s
            except Exception:
                return True
        return False

    def _ensure_fanout_role_dispatchable(
        self,
        *,
        role: RoleConfig,
        fanout_id: str,
        stage_id: str,
        child_id: str,
        run_id: str,
        trace_id: str,
        causation_id: str | None = None,
        prompt_kind: str = "fanout_child",
        skip_send_window: bool = False,
    ) -> bool:
        """Return whether a fanout role can receive a prompt now."""

        state = getattr(self, "_last_worker_state", {}).get(role.instance_id, "idle")
        if state == "busy":
            active_task_id = str(
                getattr(self, "_last_worker_task_id", {}).get(role.instance_id, "")
                or ""
            )
            active_task = self.task_store.get(active_task_id) if active_task_id else None
            if active_task is not None and active_task.status in {
                "done",
                "cancelled",
                "superseded",
            }:
                self._set_worker_state(
                    role.instance_id,
                    "idle",
                    reason="terminal canonical task released stale busy projection",
                    force=True,
                )
                state = "idle"
            elif (
                role.role_kind == "reader"
                and self._reader_fanout_busy_projection_is_stale(role.instance_id)
            ):
                self._set_worker_state(
                    role.instance_id,
                    "idle",
                    reason="terminal reader fanout released stale busy projection",
                    force=True,
                )
                state = "idle"
        try:
            dispatchable = bool(self._worker_dispatchable(role.instance_id))
        except Exception:
            dispatchable = True
        alive = True
        alive_error = ""
        try:
            alive = bool(self.transport.is_alive(role.instance_id))
        except Exception as exc:  # noqa: BLE001
            alive = False
            alive_error = str(exc)
        if alive and dispatchable:
            last = getattr(self, "_last_prompt_sent_at", {}).get(role.instance_id)
            last_key, last_sent = last or ("", 0.0)
            if (
                not skip_send_window
                and last_sent
                and last_key == run_id
                and time.monotonic() - float(last_sent) < 10.0
            ):
                self._emit_fanout_dispatch_deferred_once(
                    fanout_id=fanout_id,
                    trace_id=trace_id,
                    stage_id=stage_id,
                    child_id=child_id,
                    run_id=run_id,
                    role_instance=role.instance_id,
                    prompt_kind=prompt_kind,
                    reason="briefing_send_window_active",
                    state=state,
                    alive=alive,
                    dispatchable=dispatchable,
                    causation_id=causation_id,
                )
                return False
            if state == "busy":
                self._emit_fanout_dispatch_deferred_once(
                    fanout_id=fanout_id,
                    trace_id=trace_id,
                    stage_id=stage_id,
                    child_id=child_id,
                    run_id=run_id,
                    role_instance=role.instance_id,
                    prompt_kind=prompt_kind,
                    reason="worker_state_not_dispatchable:busy",
                    state=state,
                    alive=alive,
                    dispatchable=False,
                    causation_id=causation_id,
                )
                return False
            return True

        non_self_healing = state == "blocked_human"
        if self._fanout_dispatch_deferred_recently(
            fanout_id=fanout_id,
            child_id=child_id,
            role_instance=role.instance_id,
            window_s=900.0 if non_self_healing else 60.0,
        ):
            return False
        reason_parts: list[str] = []
        if not alive:
            reason_parts.append("worker_transport_not_alive")
        if alive_error:
            reason_parts.append(alive_error)
        if not dispatchable:
            reason_parts.append(f"worker_state_not_dispatchable:{state}")
        reason = "; ".join(reason_parts) or "worker_not_dispatchable"
        respawn_action = ""
        respawn_reason = ""
        if not alive and state != "respawning":
            try:
                decision = self._respawn_instance(role)
                respawn_action = str(getattr(decision, "action", "") or "")
                respawn_reason = str(getattr(decision, "reason", "") or "")
            except Exception as exc:  # noqa: BLE001
                respawn_action = "respawn_exception"
                respawn_reason = str(exc)
        self.event_writer.append(ZfEvent(
            type="fanout.child.dispatch_deferred",
            actor="zf-cli",
            payload={
                "fanout_id": fanout_id,
                "trace_id": trace_id,
                "stage_id": stage_id,
                "child_id": child_id,
                "run_id": run_id,
                "role_instance": role.instance_id,
                "prompt_kind": prompt_kind,
                "reason": reason,
                "worker_state": state,
                "transport_alive": alive,
                "dispatchable": dispatchable,
                "respawn_action": respawn_action,
                "respawn_reason": respawn_reason,
            },
            causation_id=causation_id,
            correlation_id=trace_id,
        ))
        return False

    def _emit_fanout_dispatch_deferred_once(
        self,
        *,
        fanout_id: str,
        trace_id: str,
        stage_id: str,
        child_id: str,
        run_id: str,
        role_instance: str,
        prompt_kind: str,
        reason: str,
        state: str,
        alive: bool,
        dispatchable: bool,
        causation_id: str | None,
    ) -> None:
        if self._fanout_dispatch_deferred_recently(
            fanout_id=fanout_id,
            child_id=child_id,
            role_instance=role_instance,
            reason=reason,
        ):
            return
        self.event_writer.append(ZfEvent(
            type="fanout.child.dispatch_deferred",
            actor="zf-cli",
            payload={
                "fanout_id": fanout_id,
                "trace_id": trace_id,
                "stage_id": stage_id,
                "child_id": child_id,
                "run_id": run_id,
                "role_instance": role_instance,
                "prompt_kind": prompt_kind,
                "reason": reason,
                "worker_state": state,
                "transport_alive": alive,
                "dispatchable": dispatchable,
            },
            causation_id=causation_id,
            correlation_id=trace_id,
        ))

    def _reader_fanout_busy_projection_is_stale(self, role_instance: str) -> bool:
        """Recognize restart-persistent reader busy state after child terminal."""

        try:
            if self._active_fanout_child_for_instance(role_instance) is not None:
                return False
            events = self.event_log.read_all()
        except Exception:
            return False
        for event in reversed(events):
            if event.type != "worker.state.changed" or event.actor != role_instance:
                continue
            payload = event.payload if isinstance(event.payload, dict) else {}
            return (
                str(payload.get("to") or "") == "busy"
                and str(payload.get("reason") or "").startswith(
                    "dispatched fanout child "
                )
            )
        return False


__all__ = ["FanoutDispatchLivenessMixin"]
