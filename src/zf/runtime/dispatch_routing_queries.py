"""Dispatch handoff/budget/role 检索查询 — K1 切片 3b(守 ≤500 再拆)。

verbatim;零裁决零状态写。"""

from __future__ import annotations

import json
import time

from zf.core.events.model import ZfEvent
from zf.core.task.schema import Task
from zf.core.config.schema import RoleConfig
from zf.core.errors import CircuitBreaker


class DispatchRoutingQueriesMixin:
    def _is_handoff_success_event(self, event: ZfEvent) -> bool:
        if event.type in self._HANDOFF_SUCCESS_EVENTS:
            return True
        return self._effective_handoff_event_type(event) in (
            self._HANDOFF_SUCCESS_EVENTS
        )

    def _effective_handoff_event_type(self, event: ZfEvent) -> str:
        if event.type != "static_gate.skipped":
            return event.type
        payload = event.payload if isinstance(event.payload, dict) else {}
        if payload.get("skipped") is True and payload.get("passed") is True:
            return "static_gate.passed"
        return event.type

    def _non_orchestrator_subscribers_for_event(
        self,
        event: ZfEvent,
    ) -> list[str]:
        return self._non_orchestrator_subscribers(
            self._effective_handoff_event_type(event)
        )

    def _requires_task_ref_for_dev_handoff(self) -> bool:
        return self._requires_task_ref_for_worktree_handoff()

    def _requires_task_ref_for_worktree_handoff(self) -> bool:
        workdirs = getattr(getattr(self.config, "runtime", None), "workdirs", None)
        return bool(
            getattr(workdirs, "enabled", False)
            and getattr(workdirs, "mode", "") == "worktree"
        )

    def _requires_task_ref_for_progress_event(self, event: ZfEvent) -> bool:
        if not self._requires_task_ref_for_worktree_handoff():
            return False
        if event.type in {"dev.build.done", "impl.child.completed"}:
            return True
        if event.type != "arch.proposal.done":
            return False
        payload = event.payload if isinstance(event.payload, dict) else {}
        return bool(
            payload.get("artifact_refs")
            or payload.get("file_plan")
            or payload.get("files")
        )

    def _handoff_already_recorded(
        self,
        *,
        task_id: str,
        target_role: str,
        progress_idx: int,
        latest_assigned: dict[str, tuple[int, str]],
        latest_dispatched: dict[str, tuple[int, str]],
    ) -> bool:
        assigned = latest_assigned.get(task_id)
        if assigned is not None:
            idx, assignee = assigned
            if idx > progress_idx and self._assignee_equivalent(assignee, target_role):
                return True
        dispatched = latest_dispatched.get(task_id)
        if dispatched is not None:
            idx, assignee = dispatched
            if idx > progress_idx and self._assignee_equivalent(assignee, target_role):
                return True
        return False

    def _assignee_equivalent(self, a: str, b: str) -> bool:
        """B-MULTIREPLICA-01: True when two assignee strings refer to
        the same worker.

        Layer 2 assigns by role.name ("dev") but Layer 1 dispatches
        using instance_id ("dev-1"). Without this normalization, the
        C3 "latest_assigned != latest_dispatched" check mis-identifies
        every multi-replica task as a pending reassignment and fires
        infinite re-dispatch loops.
        """
        if a == b:
            return True
        for role in self.config.roles:
            if (a == role.name and b == role.instance_id) or \
               (b == role.name and a == role.instance_id):
                return True
        return False

    def _latest_dispatched_per_task(self) -> dict[str, str]:
        """B-REASSIGN-DISPATCH-01 (2026-04-23): for every task seen in
        the last day of events, return the assignee of its most recent active
        ``task.dispatched`` / ``fanout.child.dispatched`` event. Empty string
        when a task was never
        dispatched or when a later ``task.assigned``/``task.requeued`` event
        superseded the dispatch (fresh reassignments land here).

        Used by the C3 WIP check to distinguish "actually in flight on
        instance X" (task.dispatched happened) from "merely reassigned
        to instance X but no briefing sent yet". Previously the check
        used task.assigned_to, which conflated both and gridlocked when
        N tasks were reassigned to the same single-replica role (they
        each saw each other as 'in flight' and all backed off).
        """
        path = getattr(self.event_log, "path", None)
        cache_key = None
        if path is not None:
            try:
                stat = path.stat()
                cache_key = (stat.st_mtime_ns, stat.st_size)
                cached = getattr(self, "_latest_dispatched_per_task_cache", None)
                if isinstance(cached, tuple) and cached[0] == cache_key:
                    return dict(cached[1])
            except OSError:
                cache_key = None
        latest: dict[str, str] = {}
        interesting = (
            '"type":"task.dispatched"',
            '"type":"fanout.child.dispatched"',
            '"type":"fanout.child.completed"',
            '"type":"fanout.child.failed"',
            '"type":"task.assigned"',
            '"type":"task.requeued"',
            '"type":"task.status_changed"',
        )
        try:
            if path is None:
                raise OSError("event log path unavailable")
            with path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    if not any(marker in line for marker in interesting):
                        continue
                    try:
                        raw = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    payload = raw.get("payload")
                    if not isinstance(payload, dict):
                        continue
                    event_type = str(raw.get("type") or "")
                    tid = str(raw.get("task_id") or payload.get("task_id") or "")
                    if not tid:
                        continue
                    if event_type in {
                        "task.dispatched",
                        "fanout.child.dispatched",
                    }:
                        assignee = (
                            payload.get("assignee")
                            or payload.get("role_instance")
                            or payload.get("role")
                            or ""
                        )
                        if assignee:
                            latest[tid] = str(assignee)
                    elif event_type in {
                        "fanout.child.completed",
                        "fanout.child.failed",
                    }:
                        latest.pop(tid, None)
                    elif event_type == "task.status_changed":
                        to_status = str(payload.get("to") or "")
                        if to_status in {"done", "cancelled", "blocked"}:
                            latest.pop(tid, None)
                    elif event_type in {"task.assigned", "task.requeued"}:
                        latest.pop(tid, None)
        except Exception:
            try:
                for event in self.event_log.read_days(1):
                    if not isinstance(event.payload, dict):
                        continue
                    tid = str(event.task_id or event.payload.get("task_id") or "")
                    if not tid:
                        continue
                    if event.type in {"task.dispatched", "fanout.child.dispatched"}:
                        a = (
                            event.payload.get("assignee")
                            or event.payload.get("role_instance")
                            or event.payload.get("role")
                            or ""
                        )
                        if a:
                            latest[tid] = a
                    elif event.type in {
                        "fanout.child.completed",
                        "fanout.child.failed",
                    }:
                        latest.pop(tid, None)
                    elif event.type == "task.status_changed":
                        to_status = str(event.payload.get("to") or "")
                        if to_status in {"done", "cancelled", "blocked"}:
                            latest.pop(tid, None)
                    elif event.type in {"task.assigned", "task.requeued"}:
                        latest.pop(tid, None)
            except Exception:
                return {}
        if cache_key is not None:
            try:
                self._latest_dispatched_per_task_cache = (cache_key, dict(latest))
            except Exception:
                pass
        return latest

    def _same_assignee_assignment_requests_dispatch(self, payload: dict) -> bool:
        """Return True when a same-assignee assignment is an explicit reissue.

        Plain same-assignee ``task.assigned`` events are often Layer 2 state
        reconciliation echoes. Treating them as pending dispatch creates a
        loop: the already-dispatched worker receives the same task again.
        """
        for key in ("redispatch", "force_dispatch", "reissue"):
            value = payload.get(key)
            if value is True:
                return True
            if isinstance(value, str) and value.strip().lower() in {
                "1",
                "true",
                "yes",
                "y",
            }:
                return True
        source = str(payload.get("source") or "").strip()
        return source in {
            "terminal_evidence_repair",
            "manual_reissue",
            "redispatch",
            "operator_reissue",
            "evidence_reissue",
            "workflow_resume_rework",
        }

    def _budget_exceeded(self, role: "RoleConfig") -> bool:
        """G-COST-BLOCK-1: return True if dispatching this role would
        exceed either the global or per-role USD cap. Emits
        cost.budget.exceeded with per-(scope, role) cooldown so the
        log doesn't flood when budget stays over."""
        import time as _time
        if not getattr(self.config, "budget_enforcement_enabled", True):
            return False
        now = _time.time()
        # Global check
        fail_closed = bool(getattr(self.config, "budget_fail_closed", False))
        global_cap = getattr(self.config, "global_budget_usd", None)
        if global_cap is not None:
            try:
                total = self.cost_tracker.total_usd()
            except Exception:
                # P0-8(审计 D9):历史 fail-open = 读失败按 $0 放行,
                # 瞄具黑屏照常开火。fail_closed 档位下按超额熔断。
                if fail_closed:
                    self._emit_cost_block(
                        scope="global_tracker_read_failed",
                        role_name=role.name,
                        budget=global_cap, current=-1.0, now=now,
                    )
                    return True
                total = 0.0
            if total >= global_cap:
                self._emit_cost_block(
                    scope="global", role_name=role.name,
                    budget=global_cap, current=total, now=now,
                )
                return True
        # Per-role check
        role_cap = getattr(role, "budget_usd", None)
        if role_cap is not None:
            try:
                role_summary = self.cost_tracker.per_role_totals().get(role.name)
                role_total = role_summary.total_usd if role_summary else 0.0
            except Exception:
                if fail_closed:
                    self._emit_cost_block(
                        scope="role_tracker_read_failed",
                        role_name=role.name,
                        budget=role_cap, current=-1.0, now=now,
                    )
                    return True
                role_total = 0.0
            if role_total >= role_cap:
                self._emit_cost_block(
                    scope="role", role_name=role.name,
                    budget=role_cap, current=role_total, now=now,
                )
                return True
        return False

    def _circuit_for(
        self, role: RoleConfig, task: Task,
    ) -> CircuitBreaker:
        """LH-4.T3: per-(role, task) breaker persisted to .zf/circuits.json.

        Max failures and window come from config defaults to keep
        per-role tuning possible later; current defaults are the same
        across roles to start simple.
        """
        return CircuitBreaker(
            key=(role.name, task.id),
            max_failures=role.max_rework_attempts + 2,
            window_seconds=1800.0,
            store_path=self.state_dir / "circuits.json",
        )

    def _worker_dispatchable(self, instance_id: str) -> bool:
        if instance_id in self._hard_cap_exceeded:
            return False
        state = getattr(self, "_last_worker_state", {}).get(instance_id, "idle")
        if state in {"pending_recycle", "recycling"} and (
            self._recover_idle_recycle_state_from_liveness(instance_id, state)
        ):
            state = getattr(self, "_last_worker_state", {}).get(instance_id, "idle")
        return state not in self._NON_DISPATCHABLE_WORKER_STATES

    def _recover_idle_recycle_state_from_liveness(
        self,
        instance_id: str,
        state: str,
    ) -> bool:
        """Clear stale recycle states when newer liveness proves idleness.

        ``worker.state.changed`` is restart truth for dispatch gating, but
        ``agent.usage``/heartbeat updates live in role_sessions. After a
        restart a worker can replay as ``pending_recycle`` while its latest
        usage already has ``current_task_id=""``. Treat that as a mechanical
        recovery only when the heartbeat is newer than the recycle state and
        no active task/fanout child still owns the instance.
        """
        if state not in {"pending_recycle", "recycling"} or not instance_id:
            return False
        try:
            from zf.core.state.role_sessions import RoleSessionRegistry

            registry = RoleSessionRegistry(
                self.state_dir / "role_sessions.yaml",
                project_root=str(self.project_root),
            )
            heartbeat_ts, heartbeat = registry.get_last_heartbeat(instance_id)
        except Exception:
            return False
        if not isinstance(heartbeat, dict):
            return False
        heartbeat_state = str(heartbeat.get("state") or "").strip()
        if heartbeat_state not in {"idle", "active", "awaiting_review"}:
            return False
        current_task_id = str(
            heartbeat.get("current_task_id") or heartbeat.get("task_id") or ""
        ).strip()
        if current_task_id:
            return False
        if not self._heartbeat_newer_than_worker_state(
            instance_id,
            heartbeat_ts or "",
        ):
            return False
        try:
            if self._active_task_for_instance(instance_id) is not None:
                return False
        except Exception:
            pass
        try:
            if self._active_fanout_child_for_instance(instance_id) is not None:
                return False
        except Exception:
            pass
        try:
            self._instance_state[instance_id] = "healthy"
        except Exception:
            pass
        try:
            self._set_worker_state(
                instance_id,
                "idle",
                reason=(
                    f"cleared stale {state} from newer idle/active liveness"
                ),
            )
        except Exception:
            return False
        return True

    def _heartbeat_newer_than_worker_state(
        self,
        instance_id: str,
        heartbeat_ts: str,
    ) -> bool:
        if not heartbeat_ts:
            return False
        latest_state_ts = ""
        try:
            events = self.event_log.read_days(1)
        except Exception:
            events = []
        for event in reversed(events):
            if event.type != "worker.state.changed":
                continue
            if str(event.actor or "") != instance_id:
                continue
            latest_state_ts = str(event.ts or "")
            break
        if not latest_state_ts:
            return True
        try:
            from datetime import datetime

            hb = datetime.fromisoformat(heartbeat_ts.replace("Z", "+00:00"))
            state_changed = datetime.fromisoformat(
                latest_state_ts.replace("Z", "+00:00")
            )
            return hb > state_changed
        except Exception:
            return heartbeat_ts > latest_state_ts

    def _find_available_role(
        self,
        task: Task,
        latest_dispatched: dict[str, str] | None = None,
    ) -> RoleConfig | None:
        """Find a role instance that can take this task.

        G-INST-4: dispatch is keyed on ``RoleConfig.instance_id``. For
        single-instance configs instance_id == name, so legacy callers
        that set ``task.assigned_to = 'dev'`` still resolve correctly.

        If the task has assigned_to set, dispatch goes to that specific
        instance. Missing instance or orchestrator role is rejected (no
        silent fallback — assignment is the source of truth).

        If assigned_to is empty (legacy / unassigned), fall back to the
        first available non-orchestrator instance.

        LH-0.T4: instances currently over the context hard cap are
        skipped so they can drain naturally (or hit the force-recycle
        path in _check_context_thresholds).

        R-TASK-STATE-AXIS-01: ``latest_dispatched`` (when supplied by
        ``_dispatch_ready``) is forwarded into ``wip.can_accept`` so
        the "active peers" count uses events-derived truth instead of
        ``assigned_to + status==in_progress``. Same generalization as
        the C3 branch fix in B-REASSIGN-DISPATCH-01.
        """
        if task.assigned_to:
            role = self._find_role_by_instance(
                task.assigned_to,
                latest_dispatched=latest_dispatched,
                route_role_name_pool=True,
            )
            if role is None or role.name == "orchestrator":
                return None
            if not self._worker_dispatchable(role.instance_id):
                return None
            if self.wip.can_accept(
                role.instance_id, self.task_store, latest_dispatched,
                equivalent=self._assignee_equivalent,
            ):
                return role
            return None  # assigned instance is busy — don't steal
        # Unassigned backlog starts at the topology entry role. Do not spill
        # the next fresh task into downstream review/test roles just because
        # the entry worker is busy; that violates pipeline order. Legacy
        # configs without trigger metadata still fall back to the first
        # available non-orchestrator role below.
        initial_role = self._initial_role_for_ready_task(task)
        if initial_role:
            role = self._find_role_by_instance(
                initial_role,
                latest_dispatched=latest_dispatched,
                route_role_name_pool=True,
            )
            if role is None or role.name == "orchestrator":
                return None
            if not self._worker_dispatchable(role.instance_id):
                return None
            if self.wip.can_accept(
                role.instance_id, self.task_store, latest_dispatched,
                equivalent=self._assignee_equivalent,
            ):
                return role
            return None

        # Unassigned with no topology metadata: first available non-orchestrator.
        for role in self.config.roles:
            if role.name == "orchestrator":
                continue
            if not self._worker_dispatchable(role.instance_id):
                continue
            if self.wip.can_accept(
                role.instance_id, self.task_store, latest_dispatched,
                equivalent=self._assignee_equivalent,
            ):
                return role
        return None

    def _find_role_by_name(self, name: str) -> RoleConfig | None:
        """Find the first role config matching ``name``.

        For multi-instance configs this returns the first replica. Used
        only for identity checks that don't care about instance (e.g.
        "is there an orchestrator role configured at all?"). Use
        ``_find_role_by_instance`` for dispatch decisions.
        """
        for role in self.config.roles:
            if role.name == name:
                return role
        return None

    def _find_role_by_instance(
        self,
        instance_id: str,
        latest_dispatched: dict[str, str] | None = None,
        *,
        route_role_name_pool: bool = False,
    ) -> RoleConfig | None:
        """Find a role config by its unique instance_id.

        B-MULTIREPLICA-01 (2026-04-22 mixed-multidev run): when Layer 2
        assigns a task by role.name ("dev") to a role with replicas>1,
        ``task.assigned_to`` ends up as "dev" — but actual instance_ids
        are "dev-1" / "dev-2". Exact match misses every cycle and the
        briefing never gets delivered.

        Fix: fall back to role.name match, preferring a WIP-available
        replica so parallel assigns distribute across the pool.
        ``_dispatch_task`` updates ``task.assigned_to`` to the concrete
        instance_id after dispatch so subsequent cycles hit the exact
        match path (this fallback only runs on first-dispatch from
        Layer 2's by-name assignment).

        R-TASK-STATE-AXIS-01: ``latest_dispatched`` is threaded through
        the WIP check so the "available replica" decision uses events-
        derived truth (vs the legacy ``assigned_to`` count which
        gridlocks under bursty reassignment).
        """
        # ``all_roles`` covers both the zf.yaml roles and runtime-spawned
        # instances (autoscale). Exact ``instance_id`` lookups must also
        # match autoscaled workers or briefings never reach them.
        roles = self.all_roles() if hasattr(self, "all_roles") else self.config.roles
        # 1) Dispatch-time role-name routing. In single-worker setups role.name and
        # instance_id are often identical ("dev"), but once replicas or
        # autoscale are active, that same value represents the whole pool.
        # Prefer any dispatchable same-name worker before treating it as an
        # exact static instance; otherwise a busy "dev" masks
        # "dev-auto-0001" forever. Keep this behind an explicit flag because
        # operator actions and lifecycle code use this helper for exact
        # instance lookup.
        name_candidates = [r for r in roles if r.name == instance_id]
        if route_role_name_pool and len(name_candidates) > 1:
            for r in name_candidates:
                if not self._worker_dispatchable(r.instance_id):
                    continue
                if self.wip.can_accept(
                    r.instance_id, self.task_store, latest_dispatched,
                    equivalent=self._assignee_equivalent,
                ):
                    return r
            return None

        # 2) Exact instance_id match (single-replica / explicit routing)
        for role in roles:
            if role.instance_id == instance_id:
                return role
        # 3) Role-name fallback for replicas>1 routing
        candidates = name_candidates
        if not candidates:
            return None
        # Prefer a WIP-available replica
        for r in candidates:
            if not self._worker_dispatchable(r.instance_id):
                continue
            if self.wip.can_accept(
                r.instance_id, self.task_store, latest_dispatched,
                equivalent=self._assignee_equivalent,
            ):
                return r
        # All replicas busy — defer to next cycle
        return None
