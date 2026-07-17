"""Agent View runtime controls: worker actions and role autoscale."""

from __future__ import annotations

import math
import subprocess
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from zf.core.config.schema import RoleConfig
from zf.core.events.model import ZfEvent
from zf.core.state.role_sessions import RoleSessionRegistry
from zf.runtime.actions_pending import (
    PERMANENT_FAILURE_EVENT,
    REQUEST_TYPES as _WORKER_ACTION_REQUESTS,
    RESULT_TYPES as _WORKER_ACTION_RESULTS,
    PendingAction,
    PendingActionsStore,
)
from zf.runtime.orchestrator_types import OrchestratorDecision

if TYPE_CHECKING:
    from zf.core.task.schema import Task


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_iso_seconds(value: str) -> float:
    if not value:
        return 0.0
    try:
        return datetime.fromisoformat(value).timestamp()
    except ValueError:
        return 0.0


class AgentViewRuntimeMixin:
    """Runtime hooks backing Agent View without adding a second control plane."""

    def _role_session_registry(self) -> RoleSessionRegistry:
        registry = getattr(self, "_role_session_registry_cache", None)
        if registry is None:
            registry = RoleSessionRegistry(
                self.state_dir / "role_sessions.yaml",
                project_root=str(self.project_root),
            )
            self._role_session_registry_cache = registry
        return registry

    def _restore_autoscaled_roles(self) -> None:
        """Rehydrate runtime-created workers from role_sessions.yaml."""
        try:
            meta_by_instance = self._role_session_registry().instance_meta()
        except Exception:
            return
        existing = {role.instance_id for role in self.all_roles()}
        for instance_id, meta in meta_by_instance.items():
            if instance_id in existing:
                continue
            if meta.get("origin") != "autoscale":
                continue
            if str(meta.get("status") or "") in {"retired", "stopped"}:
                continue
            parent_role = str(meta.get("parent_role") or meta.get("role_name") or "")
            template = self._autoscale_template_role(parent_role)
            if template is None:
                continue
            role = self._clone_autoscale_role(template, instance_id)
            self._register_runtime_role(role, parent_instance_id=template.instance_id)
            existing.add(instance_id)

    def _pending_actions_store(self) -> PendingActionsStore:
        store = getattr(self, "_pending_actions_cache", None)
        if store is None:
            store = PendingActionsStore(
                path=self.state_dir / "actions" / "pending.json",
            )
            # First touch: rebuild from the recent event tail in case the
            # projection went missing (cold start without prior state).
            try:
                if not store.path.exists():
                    events = self.event_log.read_days(7)
                    store.rebuild_from_events(events)
                else:
                    store.load()
            except Exception:
                store.load()
            self._pending_actions_cache = store
        return store

    def enqueue_worker_action_request(self, event: ZfEvent) -> None:
        """Reactor entry point — record a worker.*.requested event.

        Idempotent on ``event.id``. The orchestrator's per-tick handler
        drains the queue and emits the matching result event.
        """
        if event.type not in _WORKER_ACTION_REQUESTS:
            return
        payload = event.payload if isinstance(event.payload, dict) else {}
        instance_id = str(
            payload.get("instance_id")
            or payload.get("worker")
            or payload.get("role")
            or ""
        )
        try:
            self._pending_actions_store().upsert_pending(
                request_id=event.id,
                type=event.type,
                instance_id=instance_id,
                payload=payload,
                correlation_id=event.correlation_id,
            )
        except Exception:
            pass  # never block the reactor

    def _handle_worker_action_requests(self) -> list[OrchestratorDecision]:
        store = self._pending_actions_store()
        # Best-effort sync: pull recently emitted request events into the
        # store so requests from before we initialized still surface.
        try:
            recent_events = self.event_log.read_days(1)
            handled_request_ids = {
                str(getattr(event, "causation_id", "") or "")
                for event in recent_events
                if event.type in _WORKER_ACTION_RESULTS
                and str(getattr(event, "causation_id", "") or "")
            }
            for request_id in handled_request_ids:
                store.mark_completed(request_id)
            for event in recent_events:
                if event.type in _WORKER_ACTION_REQUESTS:
                    if event.id in handled_request_ids:
                        continue
                    self.enqueue_worker_action_request(event)
        except Exception:
            pass
        try:
            pending = store.take_pending()
        except Exception:
            return []
        decisions: list[OrchestratorDecision] = []
        for entry in pending:
            request_event = ZfEvent(
                id=entry.request_id,
                type=entry.type,
                actor="operator",
                payload=entry.payload,
                correlation_id=entry.correlation_id,
            )
            store.mark_in_flight(entry.request_id)
            try:
                if entry.type == "worker.reply.requested":
                    decision = self._apply_worker_reply_request(
                        request_event,
                        instance_id=entry.instance_id,
                        payload=entry.payload,
                    )
                elif entry.type == "worker.respawn.requested":
                    decision = self._apply_worker_respawn_request(
                        request_event, instance_id=entry.instance_id,
                    )
                else:
                    decision = self._apply_worker_drain_request(
                        request_event,
                        instance_id=entry.instance_id,
                        payload=entry.payload,
                    )
            except Exception as exc:
                self._record_action_failure(store, entry, error=str(exc))
                continue
            if decision is None:
                # Handlers already emit a `.failed` event on rejection.
                self._record_action_failure(
                    store, entry, error="handler rejected request",
                )
                continue
            store.mark_completed(entry.request_id)
            decisions.append(decision)
        return decisions

    def _record_action_failure(
        self,
        store: PendingActionsStore,
        entry: PendingAction,
        *,
        error: str,
    ) -> None:
        permanent = store.mark_failed(entry.request_id, error=error)
        if permanent:
            try:
                self.event_writer.append(ZfEvent(
                    type=PERMANENT_FAILURE_EVENT,
                    actor=entry.instance_id or "zf-cli",
                    causation_id=entry.request_id,
                    correlation_id=entry.correlation_id,
                    payload={
                        "request_id": entry.request_id,
                        "request_type": entry.type,
                        "instance_id": entry.instance_id,
                        "retries": entry.retries,
                        "last_error": error,
                    },
                ))
            except Exception:
                pass

    def _apply_worker_reply_request(
        self,
        request: ZfEvent,
        *,
        instance_id: str,
        payload: dict,
    ) -> OrchestratorDecision | None:
        role = self._find_role_by_instance(instance_id)
        message = str(payload.get("message") or payload.get("text") or "").strip()
        if role is None or not message:
            self._emit_worker_action_failed(
                request,
                "worker.reply.failed",
                instance_id=instance_id,
                reason="unknown worker or empty message",
            )
            return None
        task_id = str(payload.get("task_id") or self._active_task_id_for_worker(instance_id) or "")
        replies_dir = self.state_dir / "operator-replies"
        replies_dir.mkdir(parents=True, exist_ok=True)
        seq = len(list(replies_dir.glob(f"{instance_id}-*.md"))) + 1
        briefing_path = replies_dir / f"{instance_id}-{seq:04d}.md"
        briefing_path.write_text(
            "\n".join([
                "# Operator Reply",
                "",
                f"- worker: `{instance_id}`",
                f"- task_id: `{task_id}`" if task_id else "- task_id: ``",
                "",
                "## Message",
                message,
                "",
            ]),
            encoding="utf-8",
        )
        prompt = (
            f"Operator reply for {instance_id}.\n"
            f"Read `{briefing_path}` and continue the current task accordingly."
        )
        try:
            context = self._dispatch_context(
                role=role,
                briefing_path=briefing_path,
                task_id=task_id or None,
            )
            self._send_transport_task(instance_id, briefing_path, prompt, context)
            self.event_writer.append(ZfEvent(
                type="worker.reply.sent",
                actor="zf-cli",
                task_id=task_id or None,
                causation_id=request.id,
                correlation_id=request.correlation_id,
                payload={
                    "instance_id": instance_id,
                    "briefing": str(briefing_path),
                },
            ))
            return OrchestratorDecision(
                action="worker_reply",
                task_id=task_id or None,
                role=instance_id,
                reason="operator reply delivered",
            )
        except Exception as exc:
            self._emit_worker_action_failed(
                request,
                "worker.reply.failed",
                instance_id=instance_id,
                reason=str(exc),
            )
            return None

    def _apply_worker_respawn_request(
        self,
        request: ZfEvent,
        *,
        instance_id: str,
    ) -> OrchestratorDecision | None:
        role = self._find_role_by_instance(instance_id)
        if role is None:
            self._emit_worker_action_failed(
                request,
                "worker.respawn.failed",
                instance_id=instance_id,
                reason="unknown worker",
            )
            return None
        self.event_writer.append(ZfEvent(
            type="worker.respawn.started",
            actor=instance_id,
            causation_id=request.id,
            correlation_id=request.correlation_id,
            payload={"reason": "operator_request"},
        ))
        decision = self._respawn_instance(role)
        continuation_error = ""
        if decision.action != "respawn_failed":
            try:
                from zf.runtime.worker_respawn_continuation import (
                    deliver_respawn_continuation,
                )

                deliver_respawn_continuation(
                    self,
                    request,
                    instance_id=instance_id,
                )
            except Exception as exc:
                continuation_error = str(exc)
        event_type = (
            "worker.respawn.completed"
            if decision.action != "respawn_failed" and not continuation_error
            else "worker.respawn.failed"
        )
        self.event_writer.append(ZfEvent(
            type=event_type,
            actor=instance_id,
            causation_id=request.id,
            correlation_id=request.correlation_id,
            payload={
                "reason": continuation_error or decision.reason,
                "action": decision.action,
                "continuation_delivered": bool(
                    (request.payload or {}).get("continuation_briefing_ref")
                    and not continuation_error
                ) if isinstance(request.payload, dict) else False,
            },
        ))
        if continuation_error:
            return OrchestratorDecision(
                action="respawn_failed",
                task_id=request.task_id,
                role=instance_id,
                reason=f"respawned but continuation delivery failed: {continuation_error}",
            )
        return decision

    def _apply_worker_drain_request(
        self,
        request: ZfEvent,
        *,
        instance_id: str,
        payload: dict,
    ) -> OrchestratorDecision | None:
        role = self._find_role_by_instance(instance_id)
        if role is None:
            self._emit_worker_action_failed(
                request,
                "worker.drain.failed",
                instance_id=instance_id,
                reason="unknown worker",
            )
            return None
        reason = str(payload.get("reason") or "operator_request")
        self._set_worker_state(instance_id, "draining", reason=reason)
        if self._is_autoscaled_instance(instance_id):
            self._role_session_registry().update_instance_meta(
                instance_id,
                status="draining",
                drain_requested_at=_now_iso(),
                drain_reason=reason,
            )
        self.event_writer.append(ZfEvent(
            type="role.instance.draining",
            actor=instance_id,
            causation_id=request.id,
            correlation_id=request.correlation_id,
            payload={
                "role": role.name,
                "instance_id": instance_id,
                "reason": reason,
            },
        ))
        return OrchestratorDecision(
            action="drain",
            role=instance_id,
            reason=f"worker marked draining: {reason}",
        )

    def _emit_worker_action_failed(
        self,
        request: ZfEvent,
        event_type: str,
        *,
        instance_id: str,
        reason: str,
    ) -> None:
        try:
            self.event_writer.append(ZfEvent(
                type=event_type,
                actor=instance_id or "zf-cli",
                task_id=request.task_id,
                causation_id=request.id,
                correlation_id=request.correlation_id,
                payload={
                    "instance_id": instance_id,
                    "reason": reason,
                },
            ))
        except Exception:
            pass

    def _autoscale_workers(self) -> list[OrchestratorDecision]:
        # 2026-06-10 review P1-3: a safe-halted runtime must not keep
        # spawning replicas into the same broken environment.
        if self._runtime_safe_halted():
            return []
        self._restore_autoscaled_roles()
        decisions: list[OrchestratorDecision] = []
        for parent_role, template in self._autoscale_parent_templates().items():
            policy = template.autoscale
            if not policy.enabled:
                continue
            decisions.extend(self._autoscale_role_pool(parent_role, template))
        decisions.extend(self._retire_drained_workers())
        return decisions

    def _autoscale_parent_templates(self) -> dict[str, RoleConfig]:
        templates: dict[str, RoleConfig] = {}
        for role in self.config.roles:
            if role.name == "orchestrator" or self._is_autoscaled_instance(role.instance_id):
                continue
            templates.setdefault(role.name, role)
        return templates

    def _autoscale_role_pool(
        self,
        parent_role: str,
        template: RoleConfig,
    ) -> list[OrchestratorDecision]:
        policy = template.autoscale
        now = self._now()
        ready_tasks = self._autoscale_ready_tasks(parent_role)
        ready_count = len(ready_tasks)
        active = self._active_pool_roles(parent_role)
        current_count = len(active)
        desired = policy.min_replicas
        if ready_count > 0:
            desired = max(
                desired,
                math.ceil(ready_count / policy.target_ready_tasks_per_worker),
            )
        desired = min(desired, policy.max_replicas)
        pending_age = self._oldest_ready_age_seconds(ready_tasks, now)
        last_action = getattr(self, "_autoscale_last_action", {}).get(parent_role, 0.0)
        cooldown_ready = (now - last_action) >= policy.cooldown_seconds

        will_scale_up = (
            desired > current_count
            and cooldown_ready
            and pending_age >= policy.scale_up_pending_seconds
        )
        will_scale_down = desired < current_count and cooldown_ready
        if self._autoscale_should_emit_evaluation(
            parent_role,
            ready_count=ready_count,
            current_count=current_count,
            desired=desired,
            now=now,
            heartbeat_seconds=max(policy.cooldown_seconds, 60.0),
            decision_triggered=will_scale_up or will_scale_down,
        ):
            self.event_writer.append(ZfEvent(
                type="autoscale.evaluated",
                actor="zf-cli",
                payload={
                    "role": parent_role,
                    "ready_tasks": ready_count,
                    "current_replicas": current_count,
                    "desired_replicas": desired,
                    "max_replicas": policy.max_replicas,
                },
            ))

        if will_scale_up:
            decision = self._scale_up_role_pool(parent_role, template)
            self._mark_autoscale_action(parent_role, now)
            return [decision] if decision is not None else []

        if will_scale_down:
            decision = self._scale_down_role_pool(parent_role, template, desired)
            if decision is not None:
                self._mark_autoscale_action(parent_role, now)
                return [decision]
        return []

    def _autoscale_should_emit_evaluation(
        self,
        parent_role: str,
        *,
        ready_count: int,
        current_count: int,
        desired: int,
        now: float,
        heartbeat_seconds: float,
        decision_triggered: bool,
    ) -> bool:
        """Throttle ``autoscale.evaluated`` to state changes + heartbeats.

        Without this guard the orchestrator appends one no-op evaluation
        event per parent role per ``run_once`` tick (~17k/day at 5s tick),
        which dilutes ``events.jsonl`` and slows every reader that scans
        the log.
        """
        cache = getattr(self, "_autoscale_last_evaluation", None)
        if cache is None:
            cache = {}
            self._autoscale_last_evaluation = cache
        snapshot = (ready_count, current_count, desired)
        previous = cache.get(parent_role)
        if previous is None:
            cache[parent_role] = (snapshot, now)
            return True
        prev_snapshot, prev_ts = previous
        if snapshot != prev_snapshot or decision_triggered:
            cache[parent_role] = (snapshot, now)
            return True
        if (now - prev_ts) >= heartbeat_seconds:
            cache[parent_role] = (snapshot, now)
            return True
        return False

    def _scale_up_role_pool(
        self,
        parent_role: str,
        template: RoleConfig,
    ) -> OrchestratorDecision | None:
        instance_id = self._next_autoscale_instance_id(parent_role)
        role = self._clone_autoscale_role(template, instance_id)
        registry = self._role_session_registry()
        registry.update_instance_meta(
            instance_id,
            origin="autoscale",
            parent_role=parent_role,
            role_name=parent_role,
            role_kind=role.role_kind,
            backend=role.backend,
            template_instance_id=template.instance_id,
            status="allocated",
            allocated_at=_now_iso(),
        )
        self._register_runtime_role(role, parent_instance_id=template.instance_id)
        self.event_writer.append(ZfEvent(
            type="role.instance.allocated",
            actor="zf-cli",
            payload={
                "role": parent_role,
                "instance_id": instance_id,
                "origin": "autoscale",
                "template_instance_id": template.instance_id,
            },
        ))
        cwd = self._prepare_autoscale_spawn_cwd(role)
        try:
            self.event_writer.append(ZfEvent(
                type="autoscale.scale_up.requested",
                actor="zf-cli",
                payload={
                    "role": parent_role,
                    "instance_id": instance_id,
                    "cwd": str(cwd),
                },
            ))
            self._get_spawn_coordinator().spawn(role, cwd=cwd)
            registry.update_instance_meta(
                instance_id,
                status="active",
                spawned_at=_now_iso(),
            )
            self._set_worker_state(instance_id, "idle", reason="autoscale scale up")
            self.event_writer.append(ZfEvent(
                type="autoscale.scale_up.completed",
                actor=instance_id,
                payload={
                    "role": parent_role,
                    "instance_id": instance_id,
                    "cwd": str(cwd),
                },
            ))
            return OrchestratorDecision(
                action="scale_up",
                role=instance_id,
                reason=f"autoscaled {parent_role} to {instance_id}",
            )
        except Exception as exc:
            registry.update_instance_meta(
                instance_id,
                status="failed",
                failed_at=_now_iso(),
                failure_reason=str(exc),
            )
            self.event_writer.append(ZfEvent(
                type="autoscale.scale_up.failed",
                actor=instance_id,
                payload={
                    "role": parent_role,
                    "instance_id": instance_id,
                    "reason": str(exc),
                },
            ))
            return OrchestratorDecision(
                action="scale_up_failed",
                role=instance_id,
                reason=str(exc),
            )

    def _scale_down_role_pool(
        self,
        parent_role: str,
        template: RoleConfig,
        desired_count: int,
    ) -> OrchestratorDecision | None:
        active = self._active_pool_roles(parent_role)
        autoscaled = [
            role for role in active
            if self._is_autoscaled_instance(role.instance_id)
        ]
        if not autoscaled:
            return None
        excess = max(0, len(active) - desired_count)
        if excess <= 0:
            return None
        policy = template.autoscale
        now = self._now()
        for role in sorted(autoscaled, key=lambda item: item.instance_id, reverse=True):
            if not self._worker_idle(role.instance_id):
                continue
            if (
                self._worker_idle_duration(role.instance_id, now)
                < policy.scale_down_idle_seconds
            ):
                continue
            return self._retire_worker(role, reason="autoscale_scale_down")
        return None

    def _retire_drained_workers(self) -> list[OrchestratorDecision]:
        decisions: list[OrchestratorDecision] = []
        for role in self.all_roles():
            state = getattr(self, "_last_worker_state", {}).get(role.instance_id, "idle")
            if state != "draining" or not self._worker_idle(role.instance_id):
                continue
            decision = self._retire_worker(role, reason="drain_complete")
            if decision is not None:
                decisions.append(decision)
        return decisions

    def _retire_worker(
        self,
        role: RoleConfig,
        *,
        reason: str,
    ) -> OrchestratorDecision | None:
        if not self._is_runtime_removable_worker(role.instance_id):
            return None
        if not self._workdir_clean_for_retire(role):
            self.event_writer.append(ZfEvent(
                type="autoscale.scale_down.blocked",
                actor=role.instance_id,
                payload={
                    "role": role.name,
                    "instance_id": role.instance_id,
                    "reason": "dirty_workdir",
                },
            ))
            return None
        try:
            self.transport.terminate(role.instance_id)
        except Exception:
            pass
        # Drop the runtime-spawned role from the runtime store. We never
        # mutate ``self.config.roles`` so the zf.yaml view stays stable.
        self._runtime_roles.pop(role.instance_id, None)
        self._role_session_registry().update_instance_meta(
            role.instance_id,
            status="retired",
            retired_at=_now_iso(),
            retire_reason=reason,
        )
        self._set_worker_state(role.instance_id, "retired", reason=reason)
        self._cleanup_retired_workdir(role, reason=reason)
        self.event_writer.append(ZfEvent(
            type="autoscale.scale_down.completed",
            actor=role.instance_id,
            payload={
                "role": role.name,
                "instance_id": role.instance_id,
                "reason": reason,
            },
        ))
        return OrchestratorDecision(
            action="scale_down",
            role=role.instance_id,
            reason=f"retired {role.instance_id}: {reason}",
        )

    def _cleanup_retired_workdir(self, role: RoleConfig, *, reason: str) -> None:
        """Remove the git worktree backing a retired autoscaled worker.

        Never raises — retire must still complete even if worktree cleanup
        fails. Emits ``workdir.retired`` (or ``workdir.retire_failed``) so
        the outcome is visible in the event stream.
        """
        try:
            from zf.runtime.workdirs import WorkdirManager

            result = WorkdirManager(
                state_dir=self.state_dir,
                project_root=self.project_root,
                config=self.config,
            ).remove(role)
        except Exception as exc:
            self.event_writer.append(ZfEvent(
                type="workdir.retire_failed",
                actor="zf-cli",
                payload={
                    "role": role.name,
                    "instance_id": role.instance_id,
                    "reason": f"manager raised: {exc}",
                    "trigger": reason,
                },
            ))
            return

        event_type = (
            "workdir.retired"
            if result.removed
            else "workdir.retire_failed"
        )
        self.event_writer.append(ZfEvent(
            type=event_type,
            actor="zf-cli",
            payload={
                "role": role.name,
                "instance_id": role.instance_id,
                "workdir": result.workdir,
                "project_path": result.project_path,
                "status": result.status,
                "reason": result.reason,
                "trigger": reason,
            },
        ))

    def _register_runtime_role(
        self,
        role: RoleConfig,
        *,
        parent_instance_id: str | None = None,
    ) -> None:
        # Runtime-spawned roles live in ``_runtime_roles`` only — they
        # must not pollute ``self.config.roles`` because that field is
        # the canonical view of zf.yaml and is re-read by CLI/Web.
        if self._find_role_by_instance(role.instance_id) is None:
            self._runtime_roles[role.instance_id] = role
        registrar = getattr(self.transport, "register_role", None)
        if callable(registrar):
            registrar(role, parent_instance_id=parent_instance_id)
        detectors = getattr(self, "_stuck_detectors", None)
        if detectors is not None and role.instance_id not in detectors:
            from zf.runtime.watcher import StuckDetector

            detectors[role.instance_id] = StuckDetector(
                stale_threshold=role.stuck_threshold_seconds,
            )

    def _clone_autoscale_role(self, template: RoleConfig, instance_id: str) -> RoleConfig:
        return replace(
            template,
            instance_id=instance_id,
            replicas=1,
            backends=[],
        )

    def _autoscale_template_role(self, parent_role: str) -> RoleConfig | None:
        if not parent_role:
            return None
        for role in self.config.roles:
            if role.name == parent_role and not self._is_autoscaled_instance(role.instance_id):
                return role
        return None

    def _active_pool_roles(self, parent_role: str) -> list[RoleConfig]:
        return [
            role for role in self.all_roles()
            if role.name == parent_role
            and getattr(self, "_last_worker_state", {}).get(role.instance_id) != "retired"
        ]

    def _autoscale_ready_tasks(self, parent_role: str) -> list["Task"]:
        out: list["Task"] = []
        for task in self.task_store.ready():
            assigned = task.assigned_to or ""
            owner_role = getattr(task.contract, "owner_role", "") if task.contract else ""
            owner_instance = (
                getattr(task.contract, "owner_instance", "") if task.contract else ""
            )
            if assigned and not self._assignee_targets_parent(assigned, parent_role):
                continue
            if owner_role and owner_role != parent_role:
                continue
            if owner_instance and not self._assignee_targets_parent(
                owner_instance, parent_role,
            ):
                continue
            out.append(task)
        return out

    def _assignee_targets_parent(self, assignee: str, parent_role: str) -> bool:
        if assignee == parent_role:
            return True
        for role in self.all_roles():
            if role.name == parent_role and assignee == role.instance_id:
                return True
        return assignee.startswith(f"{parent_role}-")

    def _oldest_ready_age_seconds(self, tasks: list["Task"], now: float) -> float:
        if not tasks:
            return 0.0
        created = [
            _parse_iso_seconds(getattr(task, "created_at", "") or "")
            for task in tasks
        ]
        created = [value for value in created if value > 0]
        if not created:
            return 0.0
        return max(0.0, now - min(created))

    def _mark_autoscale_action(self, parent_role: str, now: float) -> None:
        cache = getattr(self, "_autoscale_last_action", None)
        if cache is None:
            cache = {}
            self._autoscale_last_action = cache
        cache[parent_role] = now

    def _next_autoscale_instance_id(self, parent_role: str) -> str:
        try:
            meta_keys = set(self._role_session_registry().instance_meta())
        except Exception:
            meta_keys = set()
        existing = {role.instance_id for role in self.all_roles()} | meta_keys
        for index in range(1, 10_000):
            candidate = f"{parent_role}-auto-{index:04d}"
            if candidate not in existing:
                return candidate
        raise RuntimeError(f"could not allocate autoscale instance for {parent_role}")

    def _is_autoscaled_instance(self, instance_id: str) -> bool:
        if "-auto-" in instance_id:
            return True
        try:
            meta = self._role_session_registry().instance_meta().get(instance_id, {})
        except Exception:
            return False
        return meta.get("origin") == "autoscale"

    def _is_runtime_removable_worker(self, instance_id: str) -> bool:
        return self._is_autoscaled_instance(instance_id)

    def _worker_idle(self, instance_id: str) -> bool:
        if getattr(self, "_last_worker_state", {}).get(instance_id, "idle") in {
            "busy",
            "respawning",
            "recycling",
            "pending_recycle",
        }:
            self._clear_worker_idle_since(instance_id)
            return False
        if not self.wip.can_accept(instance_id, self.task_store):
            self._clear_worker_idle_since(instance_id)
            return False
        for task in self.task_store.list_all():
            if task.status == "in_progress" and task.assigned_to == instance_id:
                self._clear_worker_idle_since(instance_id)
                return False
        return True

    def _worker_idle_duration(self, instance_id: str, now: float) -> float:
        idle_since = getattr(self, "_autoscale_idle_since", None)
        if idle_since is None:
            idle_since = {}
            self._autoscale_idle_since = idle_since
        if instance_id not in idle_since:
            idle_since[instance_id] = now
        return max(0.0, now - idle_since[instance_id])

    def _clear_worker_idle_since(self, instance_id: str) -> None:
        idle_since = getattr(self, "_autoscale_idle_since", None)
        if idle_since is not None:
            idle_since.pop(instance_id, None)

    def _active_task_id_for_worker(self, instance_id: str) -> str:
        for task in self.task_store.list_all():
            if task.status == "in_progress" and task.assigned_to == instance_id:
                return task.id
        return ""

    def _prepare_autoscale_spawn_cwd(self, role: RoleConfig) -> Path:
        try:
            from zf.runtime.workdirs import WorkdirManager

            plan = WorkdirManager(
                state_dir=self.state_dir,
                project_root=self.project_root,
                config=self.config,
            ).prepare(role)
            self.event_writer.append(ZfEvent(
                type="workdir.prepared",
                actor="zf-cli",
                payload={
                    "role": role.name,
                    "instance_id": role.instance_id,
                    "workdir": plan.workdir,
                    "project_path": plan.project_path,
                    "mode": plan.mode,
                    "enabled": plan.enabled,
                    "source": "autoscale",
                },
            ))
            project_path = Path(plan.project_path)
            if plan.enabled and plan.mode == "worktree" and project_path.exists():
                return project_path
        except Exception as exc:
            self.event_writer.append(ZfEvent(
                type="workdir.prepare_failed",
                actor="zf-cli",
                payload={
                    "role": role.name,
                    "instance_id": role.instance_id,
                    "reason": str(exc),
                    "source": "autoscale",
                },
            ))
        return self.project_root

    def _workdir_clean_for_retire(self, role: RoleConfig) -> bool:
        try:
            from zf.runtime.workdirs import WorkdirManager

            plan = WorkdirManager(
                state_dir=self.state_dir,
                project_root=self.project_root,
                config=self.config,
            ).plan(role)
            project_path = Path(plan.project_path)
            if not plan.enabled or plan.mode != "worktree" or not project_path.exists():
                return True
            result = subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=project_path,
                check=False,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            )
            return result.returncode == 0 and not result.stdout.strip()
        except Exception:
            return True
