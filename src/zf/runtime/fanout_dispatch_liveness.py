"""Shared availability and liveness fence for fanout child dispatch."""

from __future__ import annotations

from datetime import datetime, timezone
import time

from zf.core.config.schema import RoleConfig
from zf.core.events.model import ZfEvent


class FanoutDispatchLivenessMixin:
    def _reader_fanout_context_scope(
        self,
        role_instance: str,
    ) -> tuple[str, str]:
        """Return the workflow/task scope bound to a reader provider context."""

        try:
            meta = self._role_lifecycle_registry().instance_meta().get(
                role_instance,
                {},
            )
        except Exception:
            meta = {}
        bound_task_id = str(meta.get("fanout_context_task_id") or "").strip()
        bound_trace_id = str(meta.get("fanout_context_trace_id") or "").strip()
        if bound_task_id or bound_trace_id:
            return bound_task_id, bound_trace_id

        try:
            events = self._fanout_lifecycle_events()
        except Exception:
            return "", ""
        for event in reversed(events):
            if event.type not in {
                "fanout.child.dispatched",
                "fanout.synth.dispatched",
            }:
                continue
            payload = event.payload if isinstance(event.payload, dict) else {}
            if str(payload.get("role_instance") or "").strip() != role_instance:
                continue
            task_id = str(event.task_id or payload.get("task_id") or "").strip()
            trace_id = str(
                payload.get("workflow_run_id")
                or payload.get("trace_id")
                or event.correlation_id
                or ""
            ).strip()
            if task_id or trace_id:
                return task_id, trace_id
        return "", ""

    @staticmethod
    def _reader_fanout_context_matches(
        previous: tuple[str, str],
        current: tuple[str, str],
    ) -> bool:
        previous_task_id, previous_trace_id = previous
        task_id, trace_id = current
        if previous_task_id != task_id:
            return False
        return not (
            previous_trace_id
            and trace_id
            and previous_trace_id != trace_id
        )

    def _prepare_reader_fanout_context(
        self,
        *,
        role: RoleConfig,
        task_id: str,
        trace_id: str,
        state: str,
        provider_session_replaced: bool,
    ) -> tuple[bool, dict[str, str]]:
        """Rotate an idle reader before it crosses a root workflow boundary."""

        if (
            role.role_kind != "reader"
            or not self._role_is_on_demand(role)
            or not task_id
        ):
            return True, {}
        registry = self._role_lifecycle_registry()
        current = (task_id, trace_id)
        previous = self._reader_fanout_context_scope(role.instance_id)
        if provider_session_replaced or not any(previous):
            registry.update_instance_meta(
                role.instance_id,
                fanout_context_task_id=task_id,
                fanout_context_trace_id=trace_id,
            )
            return True, {}
        if self._reader_fanout_context_matches(previous, current):
            return True, {}

        blockers: list[str] = []
        if self._fanout_role_has_active_provider_turn(role.instance_id):
            blockers.append("provider_turn_active")
        if self._active_fanout_child_for_instance(role.instance_id) is not None:
            blockers.append("fanout_child_active")
        active_task = self._active_task_for_instance(role.instance_id)
        if active_task is not None:
            blockers.append(f"task_active:{active_task.id}")
        if state in {
            "blocked_human",
            "busy",
            "pending_recycle",
            "recycling",
            "respawning",
        }:
            blockers.append(f"worker_state:{state}")
        if blockers:
            return False, {
                "defer_reason": "reader_context_switch_waiting_for_settlement:"
                + ",".join(blockers),
            }

        old_session = registry.get(role.instance_id)
        rotation = {
            "previous_task_id": previous[0],
            "previous_trace_id": previous[1],
            "task_id": task_id,
            "trace_id": trace_id,
            "old_session": str(old_session) if old_session else "",
        }
        self.event_writer.append(ZfEvent(
            type="worker.recycling",
            actor=role.instance_id,
            task_id=task_id,
            payload={
                "role": role.name,
                "instance_id": role.instance_id,
                "backend": role.backend,
                "reason": "reader_root_context_changed",
                **rotation,
            },
            correlation_id=trace_id or None,
        ))
        try:
            if self.transport.is_alive(role.instance_id):
                self.transport.terminate(role.instance_id)
        except Exception as exc:  # noqa: BLE001
            self.event_writer.append(ZfEvent(
                type="worker.recycle.failed",
                actor=role.instance_id,
                task_id=task_id,
                payload={
                    "role": role.name,
                    "instance_id": role.instance_id,
                    "reason": "reader_root_context_changed",
                    "error": str(exc)[:500],
                    **rotation,
                },
                correlation_id=trace_id or None,
            ))
            return False, {
                "defer_reason": f"reader_context_switch_terminate_failed:{exc}",
            }

        if role.backend == "codex":
            registry.clear(role.instance_id)
            session_strategy = "reader_task_boundary_clear_codex"
            new_session = ""
        else:
            new_session = str(registry.rotate(role.instance_id))
            session_strategy = "reader_task_boundary_rotate_session"
        now = datetime.fromtimestamp(self._now(), tz=timezone.utc).isoformat()
        registry.update_instance_meta(
            role.instance_id,
            fanout_context_task_id=task_id,
            fanout_context_trace_id=trace_id,
            fanout_context_previous_task_id=previous[0],
            fanout_context_previous_trace_id=previous[1],
            fanout_context_rotated_at=now,
            lifecycle_state="suspended",
            lifecycle_transition_at=now,
            lifecycle_suspended_at=now,
            lifecycle_last_error="",
        )
        self._set_worker_state(
            role.instance_id,
            "suspended",
            reason="reader provider context rotated for a new root workflow",
            task_id=task_id,
            force=True,
        )
        return True, {
            **rotation,
            "new_session": new_session,
            "session_strategy": session_strategy,
        }

    @staticmethod
    def _reader_dispatch_lost_event_after(
        role_events: list[tuple[int, ZfEvent]],
        dispatch_index: int,
    ) -> ZfEvent | None:
        for index, event in reversed(role_events):
            if index > dispatch_index:
                return event
        return None

    @staticmethod
    def _fanout_dispatch_reinjected_after_launch(
        events: list[ZfEvent],
        dispatch: ZfEvent,
        lost_index: int,
    ) -> bool:
        """Avoid resending a briefing already handed to the replacement worker."""
        if not 0 <= lost_index < len(events):
            return False
        if events[lost_index].type != "worker.launch_artifact.written":
            return False
        expected = {key: str(dispatch.payload.get(key) or "") for key in (
            "fanout_id", "child_id", "run_id", "stage_id", "briefing_path",
        )}
        role = str(dispatch.payload.get("role_instance") or "")
        trace = str(dispatch.payload.get("trace_id") or dispatch.correlation_id or "")
        required = ("fanout_id", "run_id", "stage_id", "briefing_path")
        if not role or not trace or not all(expected[key] for key in required):
            return False
        if dispatch.type == "fanout.child.dispatched" and not expected["child_id"]:
            return False
        for event in events[lost_index + 1:]:
            if (
                event.type == "worker.recovery.injected"
                and event.actor == role
                and event.correlation_id == trace
                and all(event.payload.get(key) == value for key, value in expected.items())
            ):
                return True
        return False

    @staticmethod
    def _fanout_dispatch_has_authoritative_result(
        events: list[ZfEvent],
        dispatch_event: ZfEvent,
    ) -> bool:
        """Return whether durable call-result truth already settled a dispatch."""

        dispatch_payload = (
            dispatch_event.payload
            if isinstance(dispatch_event.payload, dict)
            else {}
        )
        operation_id = str(dispatch_payload.get("operation_id") or "").strip()
        request_hash = str(dispatch_payload.get("request_hash") or "").strip()
        if not operation_id:
            return False
        for event in events:
            if event.type not in {
                "workflow.call.result.admitted",
                "workflow.operation.settled",
            }:
                continue
            payload = event.payload if isinstance(event.payload, dict) else {}
            if str(payload.get("operation_id") or "").strip() != operation_id:
                continue
            result_hash = str(payload.get("request_hash") or "").strip()
            if request_hash and result_hash and result_hash != request_hash:
                continue
            if event.type == "workflow.call.result.admitted":
                return True
            result_ref = payload.get("admitted_call_result_ref")
            if isinstance(result_ref, dict) and str(
                result_ref.get("ref") or ""
            ).strip():
                return True
        return False

    def _defer_fanout_retry_dispatch(
        self,
        manifest: dict,
        child: dict,
        role: RoleConfig,
        run_id: str,
        attempt: int,
        previous_dispatch: ZfEvent,
        reason: str,
    ) -> None:
        """Record an admission-blocked retry without failing the child."""

        trace_id = str(manifest.get("trace_id") or "")
        payload = {
            "fanout_id": str(manifest.get("fanout_id") or ""),
            "trace_id": trace_id,
            "stage_id": str(manifest.get("stage_id") or ""),
            "child_id": str(child.get("child_id") or ""),
            "run_id": run_id,
            "role_instance": role.instance_id,
            "task_id": str(child.get("task_id") or ""),
            "retry_of_run_id": str(
                previous_dispatch.payload.get("run_id") or ""
            ),
            "attempt": attempt + 1,
            "reason": reason,
            "failure_kind": "run_dispatch_blocked",
        }
        self._copy_fanout_assignment_metadata(payload, child)
        self.event_writer.append(ZfEvent(
            type="fanout.child.dispatch_deferred",
            actor="zf-cli",
            task_id=payload["task_id"] or None,
            payload=payload,
            causation_id=previous_dispatch.id,
            correlation_id=trace_id,
        ))

    def _fanout_role_has_active_provider_turn(
        self,
        role_instance: str,
    ) -> bool:
        try:
            return self._active_provider_turn(role_instance) is not None
        except Exception:
            return False

    @staticmethod
    def _reader_dispatch_lost_role(event: ZfEvent) -> str:
        payload = event.payload if isinstance(event.payload, dict) else {}
        if (
            event.type == "role.lifecycle.dormant"
            and str(payload.get("to") or "").strip() == "dormant"
        ):
            return str(
                payload.get("instance_id")
                or payload.get("role")
                or event.actor
                or ""
            ).strip()
        if (
            event.type == "provider.turn.closed"
            and str(payload.get("backend") or "").strip() == "codex"
            and str(payload.get("turn_id") or "").strip()
        ):
            return str(
                payload.get("instance_id")
                or payload.get("role")
                or event.actor
                or ""
            ).strip()
        if event.type == "worker.refresh.triggered":
            reason = str(payload.get("reason") or "").strip().lower()
            if reason in {"drift", "context_pressure", "task_complete"}:
                return ""
            return str(
                payload.get("role")
                or payload.get("instance_id")
                or event.actor
                or ""
            ).strip()
        if event.type == "cost.usage.capture_miss":
            reason = str(payload.get("reason") or "")
            if "session file not found" in reason:
                return str(
                    event.actor or payload.get("role") or ""
                ).strip()
        if event.type == "worker.launch_artifact.written":
            try:
                launch_attempt = int(payload.get("launch_attempt") or 0)
            except (TypeError, ValueError):
                launch_attempt = 0
            if launch_attempt > 1:
                return str(
                    payload.get("instance_id")
                    or payload.get("role")
                    or event.actor
                    or ""
                ).strip()
        return ""

    @staticmethod
    def _fanout_role_has_activity_after_signal(
        events: list[ZfEvent],
        role_instance: str,
        signal_index: int,
    ) -> bool:
        """Return whether a role produced fresh provider output after a signal.

        Disk usage readers can append an old provider sample after a worker was
        relaunched. Event ordering alone would then treat stale usage as proof
        that the replacement session accepted the in-flight briefing.
        """

        activity_types = {
            "agent.usage",
            "agent.text",
            "agent.tool.use",
            "agent.tool.result",
            "refactor.scan.completed",
            "verify.child.completed",
            "verify.child.failed",
            "judge.passed",
            "judge.failed",
        }
        signal_epoch = (
            _iso_epoch(events[signal_index].ts)
            if 0 <= signal_index < len(events)
            else None
        )
        for index, event in enumerate(events):
            if index <= signal_index or event.type not in activity_types:
                continue
            payload = event.payload if isinstance(event.payload, dict) else {}
            if event.type == "agent.usage" and str(
                payload.get("source") or ""
            ) == "disk_reader":
                usage_epoch = _iso_epoch(payload.get("usage_timestamp"))
                if (
                    usage_epoch is None
                    or signal_epoch is None
                    or usage_epoch <= signal_epoch
                ):
                    continue
            if str(event.actor or "").strip() == role_instance:
                return True
            if str(payload.get("role_instance") or "").strip() == role_instance:
                return True
            if str(payload.get("role") or "").strip() == role_instance:
                return True
        return False

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
        for index in range(len(events) - 1, -1, -1):
            event = events[index]
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
            if reason == "provider_turn_active":
                return not self._provider_turn_defer_resolved(
                    events[index + 1 :],
                    role_instance=role_instance,
                )
            try:
                return now - self._event_epoch(event) < window_s
            except Exception:
                return True
        return False

    @staticmethod
    def _provider_turn_defer_resolved(
        events: list[ZfEvent],
        *,
        role_instance: str,
    ) -> bool:
        """Return whether a durable event released the deferred provider turn."""

        for event in events:
            payload = event.payload if isinstance(event.payload, dict) else {}
            if event.type == "loop.stopped":
                return True
            matches_role = role_instance in {
                str(event.actor or "").strip(),
                str(payload.get("instance_id") or "").strip(),
                str(payload.get("role_instance") or "").strip(),
            }
            if not matches_role:
                continue
            if event.type in {
                "codex.hook.stop",
                "provider.turn.closed",
                "role.lifecycle.dormant",
                "worker.launch_artifact.written",
                "worker.recycled",
                "worker.respawned",
            }:
                return True
        return False

    def _fail_exhausted_reader_fanout_dispatch(
        self,
        *,
        manifest: dict,
        child: dict,
        lost_event: ZfEvent,
        loss_reason: str,
    ) -> None:
        fanout_id = str(manifest.get("fanout_id") or "")
        child_id = str(child.get("child_id") or "")
        role_instance = str(child.get("role_instance") or "")
        run_id = str(child.get("run_id") or "")
        task_id = str(child.get("task_id") or "")
        trace_id = str(manifest.get("trace_id") or "")
        self.event_writer.append(ZfEvent(
            type="fanout.child.failed",
            actor="zf-cli",
            task_id=task_id or None,
            payload={
                "fanout_id": fanout_id,
                "trace_id": trace_id,
                "stage_id": str(manifest.get("stage_id") or ""),
                "child_id": child_id,
                "run_id": run_id,
                "role_instance": role_instance,
                "task_id": task_id,
                "reason": f"{loss_reason}_recovery_exhausted",
                "failure_class": "worker_noop_or_terminal_missing",
                "lost_signal_event_id": lost_event.id,
                "lost_signal_type": lost_event.type,
            },
            causation_id=lost_event.id,
            correlation_id=trace_id,
        ))
        self._release_fanout_worker_if_terminal(
            role_instance=role_instance,
            fanout_id=fanout_id,
            child_id=child_id,
            run_id=run_id,
            task_id=task_id,
        )
        self._evaluate_reader_fanout(fanout_id)

    def _ensure_fanout_role_dispatchable(
        self,
        *,
        role: RoleConfig,
        fanout_id: str,
        stage_id: str,
        child_id: str,
        run_id: str,
        trace_id: str,
        task_id: str | None = None,
        causation_id: str | None = None,
        prompt_kind: str = "fanout_child",
        skip_send_window: bool = False,
        provider_session_replaced: bool = False,
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

        context_ready, context_rotation = self._prepare_reader_fanout_context(
            role=role,
            task_id=str(task_id or "").strip(),
            trace_id=str(trace_id or "").strip(),
            state=state,
            provider_session_replaced=provider_session_replaced,
        )
        if not context_ready:
            try:
                context_transport_alive = bool(
                    self.transport.is_alive(role.instance_id)
                )
            except Exception:
                context_transport_alive = False
            self._emit_fanout_dispatch_deferred_once(
                fanout_id=fanout_id,
                trace_id=trace_id,
                stage_id=stage_id,
                child_id=child_id,
                run_id=run_id,
                role_instance=role.instance_id,
                prompt_kind=prompt_kind,
                reason=str(
                    context_rotation.get("defer_reason")
                    or "reader_context_switch_deferred"
                ),
                state=state,
                alive=context_transport_alive,
                dispatchable=False,
                causation_id=causation_id,
            )
            return False

        activation_error = ""
        if self._role_is_on_demand(role):
            try:
                self._ensure_role_active(role, task_id=task_id)
            except Exception as exc:  # noqa: BLE001
                activation_error = str(exc)
        if context_rotation and not activation_error:
            registry = self._role_lifecycle_registry()
            observed_session = registry.get(role.instance_id)
            self.event_writer.append(ZfEvent(
                type="worker.recycled",
                actor=role.instance_id,
                task_id=task_id,
                payload={
                    "role": role.name,
                    "instance_id": role.instance_id,
                    "backend": role.backend,
                    "reason": "reader_root_context_changed",
                    **context_rotation,
                    "new_session": (
                        str(observed_session)
                        if observed_session
                        else context_rotation.get("new_session", "")
                    ),
                },
                correlation_id=trace_id or None,
            ))
            getattr(self, "_instance_state", {})[role.instance_id] = "healthy"
            provider_session_replaced = True
        state = getattr(self, "_last_worker_state", {}).get(role.instance_id, state)
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
        if alive and dispatchable and not activation_error:
            if (
                not provider_session_replaced
                and self._fanout_role_has_active_provider_turn(role.instance_id)
            ):
                self._emit_fanout_dispatch_deferred_once(
                    fanout_id=fanout_id,
                    trace_id=trace_id,
                    stage_id=stage_id,
                    child_id=child_id,
                    run_id=run_id,
                    role_instance=role.instance_id,
                    prompt_kind=prompt_kind,
                    reason="provider_turn_active",
                    state=state,
                    alive=alive,
                    dispatchable=dispatchable,
                    causation_id=causation_id,
                )
                return False
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
        if activation_error:
            reason_parts.append(f"role_activation_failed:{activation_error}")
        if alive_error:
            reason_parts.append(alive_error)
        if not dispatchable:
            reason_parts.append(f"worker_state_not_dispatchable:{state}")
        reason = "; ".join(reason_parts) or "worker_not_dispatchable"
        respawn_action = ""
        respawn_reason = ""
        if not alive and state != "respawning" and not activation_error:
            try:
                decision = self._respawn_instance(
                    role,
                    inject_idle_prompt=False,
                )
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


def _iso_epoch(value: object) -> float | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return None
