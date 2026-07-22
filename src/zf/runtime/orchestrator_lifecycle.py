"""LifecycleManagerMixin — worker watchdog, respawn, recycle, drift detection.

Split from orchestrator.py (P1.2). Methods rely on Orchestrator's
shared state (self._dead_counter, self._stuck_detectors,
self._instance_state, self._refresh_policy, self._gan_round, etc.)
and shared services (self.event_log, self.transport, self.task_store,
self.cost_tracker, self.config, self.state_dir).

The Mixin pattern is used (rather than composition) because the 11
methods share 16+ state fields with the rest of Orchestrator, and a
real component split would force every cross-call to relay state via
the parent. Mixin keeps the existing behaviour exactly while shrinking
orchestrator.py from 1571 → ~1000 lines.
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import asdict
from pathlib import Path
from typing import TYPE_CHECKING

from zf.core.config.schema import RoleConfig
from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.core.state.role_sessions import RoleSessionRegistry
from zf.core.task.schema import Task
from zf.runtime.injection import (
    build_task_prompt,
    generate_role_instructions,
)
from zf.runtime.cli_command import zf_cli_cmd
from zf.runtime.provider_context import has_provider_context_exhausted
from zf.runtime.recovery import build_recovery_briefing
from zf.runtime.remediation_cascade import (
    CASCADE_SAFE_HALT,
    SAFE_HALTED_EVENT,
    build_safe_halt_payload,
    decide_cascade,
)
from zf.runtime.recovery_sufficiency import run_recovery_sufficiency_gate
from zf.runtime.session_mutex import SessionLock, SessionLockBusy
from zf.runtime.orchestrator_types import OrchestratorDecision

if TYPE_CHECKING:
    from zf.runtime.spawn_coordinator import SpawnCoordinator

# SpawnCoordinator is imported lazily inside _get_spawn_coordinator below.


from zf.runtime.lifecycle_evidence_queries import (
    LifecycleEvidenceQueriesMixin,
)


from zf.runtime.lifecycle_observation import LifecycleObservationMixin


from zf.runtime.lifecycle_liveness_evidence import (
    LifecycleLivenessEvidenceMixin,
)


class LifecycleManagerMixin(
    LifecycleEvidenceQueriesMixin,
    LifecycleObservationMixin,
    LifecycleLivenessEvidenceMixin,
):
    """Lifecycle methods of Orchestrator (worker watchdog, recycle,
    refresh triggers, drift detection, recovery briefing, orphan
    timeouts). Mixin contract: relies on the host Orchestrator's
    instance fields listed in the module docstring. Do not instantiate
    standalone.
    """
    _STUCK_RECOVERY_PROGRESS_EVENTS: frozenset[str] = frozenset({
        "arch.proposal.done",
        "design.critique.done",
        "dev.build.done",
        "dev.blocked",
        "review.approved",
        "review.rejected",
        "verify.passed",
        "verify.failed",
        "test.passed",
        "test.failed",
        "judge.passed",
        "judge.failed",
        "gate.failed",
        "discriminator.failed",
    })
    _CODEX_ACTIVE_TURN_STUCK_GRACE_SECONDS = 900.0

    @property
    def event_writer(self) -> EventWriter:
        writer = getattr(self, "_event_writer", None)
        if writer is None:
            writer = EventWriter(self.event_log)
            self._event_writer = writer
        return writer

    @event_writer.setter
    def event_writer(self, writer: EventWriter) -> None:
        self._event_writer = writer

    def _now(self) -> float:
        """LH-0.T3: time source for orphan / timeout checks. Tests
        override this to inject deterministic timestamps."""
        return time.time()

    def _check_unclaimed_new_tasks(self) -> None:
        """B18 (doc 93 §7.4): created 无认领无派发超 SLA →
        task.unclaimed.warning(幂等)。best-effort,不挡 tick。"""
        try:
            from zf.runtime.new_task_shepherd import unclaimed_warnings

            events = list(self.event_log.read_days(1))
            for payload in unclaimed_warnings(
                self.task_store.list_all(), events, now_ts=self._now(),
            ):
                self.event_writer.append(ZfEvent(
                    type="task.unclaimed.warning",
                    actor="zf-cli",
                    task_id=str(payload.get("task_id") or ""),
                    payload=payload,
                ))
        except Exception:
            pass

    def _check_orphaned_tasks(self) -> None:
        """LH-0.T3: scan in_progress tasks; if none of their stage-
        completion events arrived within ``orphan_warning_seconds`` of
        the most recent dispatch/stage-progress, emit orphan_warning;
        past ``orphan_escalate_seconds`` → task.orphaned + escalate +
        requeue (clear assignee, status → backlog) so next wake can
        reassign.

        Uses host ``self._dispatch_epoch`` (per-task float timestamps
        set by _dispatch_task on new dispatch and by the stage-progress
        housekeeping handler on forward progress) and ``_orphan_warned``
        for per-task dedup of the warning event.
        """
        now = self._now()
        try:
            latest_dispatched = self._latest_dispatched_per_task()
        except Exception:
            latest_dispatched = {}
        for task in self.task_store.list_all():
            if task.status != "in_progress":
                # Not in_progress → not orphan-eligible. Also clear any
                # prior warning mark so reentering in_progress gets a
                # fresh clock.
                self._orphan_warned.discard(task.id)
                continue
            epoch = self._dispatch_epoch.get(task.id)
            if epoch is None:
                continue  # no dispatch record — legacy / test-fixture task
            assignee = task.assigned_to or ""
            worker_id = latest_dispatched.get(task.id, "")
            # Pick per-role timeouts when we know the role; fall back to
            # the first role's defaults (same values by default).
            role = self._find_role_by_instance(worker_id) if worker_id else None
            if role is None and assignee:
                role = (
                    self._find_role_by_instance(assignee)
                    or self._find_role_by_name(assignee)
                )
            if role is None:
                role = next(iter(self.config.roles), None)
            if role is None:
                continue
            worker_activity = getattr(self, "_worker_activity_epoch", {})
            activity_keys = [key for key in (worker_id, assignee) if key]
            last_activity = max(
                (
                    worker_activity[key]
                    for key in activity_keys
                    if key in worker_activity
                ),
                default=None,
            )
            if last_activity is not None and last_activity > epoch:
                self._dispatch_epoch[task.id] = last_activity
                self._orphan_warned.discard(task.id)
                epoch = last_activity
            dispatch_id = getattr(task, "active_dispatch_id", "") or ""
            if not dispatch_id:
                try:
                    dispatch_event = self._latest_dispatch_event_for_task(task.id)
                    if dispatch_event and isinstance(dispatch_event.payload, dict):
                        dispatch_id = str(
                            dispatch_event.payload.get("dispatch_id") or ""
                        )
                except Exception:
                    dispatch_id = ""
            try:
                progressed = self._latest_unrejected_progress_event_for_dispatch(
                    task.id,
                    dispatch_id,
                )
            except Exception:
                progressed = None
            if progressed is not None:
                self._dispatch_epoch[task.id] = now
                self._orphan_warned.discard(task.id)
                continue
            elapsed = now - epoch
            if elapsed >= role.orphan_escalate_seconds:
                self._emit_task_orphaned(task, role, elapsed)
                # After escalate, clear tracking so the clock restarts
                # when someone reassigns.
                self._dispatch_epoch.pop(task.id, None)
                self._orphan_warned.discard(task.id)
            elif elapsed >= role.orphan_warning_seconds:
                if task.id in self._orphan_warned:
                    continue
                self._orphan_warned.add(task.id)
                try:
                    self.event_writer.append(ZfEvent(
                        type="task.orphan_warning",
                        actor="zf-cli",
                        task_id=task.id,
                        payload={
                            "role": role.name,
                            "assigned_to": worker_id or assignee,
                            "elapsed_seconds": round(elapsed, 1),
                            "threshold_seconds": role.orphan_warning_seconds,
                        },
                    ))
                except Exception:
                    pass

    def _emit_task_orphaned(
        self, task: Task, role: RoleConfig, elapsed: float,
    ) -> None:
        """LH-0.T3: full orphan escalation — emit, unassign, requeue."""
        try:
            self.event_writer.append(ZfEvent(
                type="task.orphaned",
                actor="zf-cli",
                task_id=task.id,
                payload={
                    "role": role.name,
                    "assigned_to": task.assigned_to or "",
                    "elapsed_seconds": round(elapsed, 1),
                    "threshold_seconds": role.orphan_escalate_seconds,
                },
            ))
        except Exception:
            pass
        try:
            self.escalation.escalate(
                f"task {task.id}: orphaned "
                f"({elapsed:.0f}s > {role.orphan_escalate_seconds:.0f}s)"
            )
        except Exception:
            pass
        # Requeue for future dispatch. Clearing assigned_to also frees
        # the WIP slot for that instance.
        try:
            self.task_store.update(
                task.id, status="backlog", assigned_to="",
            )
        except Exception:
            pass

    def _get_spawn_coordinator(self) -> "SpawnCoordinator":
        """Lazily build the coordinator (avoids circular init)."""
        if self._spawn_coordinator is None:
            from zf.runtime.spawn_coordinator import SpawnCoordinator
            registry = RoleSessionRegistry(
                self.state_dir / "role_sessions.yaml",
                project_root=str(self.project_root),
            )
            self._spawn_coordinator = SpawnCoordinator(
                state_dir=self.state_dir,
                registry=registry,
                transport=self.transport,
                project_root=str(self.project_root),
                event_log=self.event_log,
                config=self.config,
            )
        return self._spawn_coordinator

    def _role_spawn_cwd(self, role: "RoleConfig", *, source: str) -> Path | None:
        """Resolve cwd for lifecycle-managed spawns.

        Initial `zf start` launches workers from their configured worktree
        project paths. Respawn/recycle must preserve that invariant; otherwise
        a recovered writer silently falls back to the harness root.
        """
        workdirs = getattr(getattr(self.config, "runtime", None), "workdirs", None)
        if workdirs is None or not getattr(workdirs, "enabled", False):
            return None

        from zf.runtime.workdirs import WorkdirManager

        manager = WorkdirManager(
            state_dir=self.state_dir,
            project_root=self.project_root,
            config=self.config,
        )
        plan = manager.prepare(role)
        try:
            payload = asdict(plan)
            payload["source"] = source
            self.event_writer.append(ZfEvent(
                type="workdir.prepared",
                actor="zf-cli",
                payload=payload,
            ))
        except Exception:
            pass

        project_path = Path(plan.project_path)
        if (
            plan.enabled
            and plan.mode == "worktree"
            and plan.role_kind in {"writer", "reader"}
        ):
            if not project_path.exists():
                raise RuntimeError(
                    f"workdir project path missing for {role.instance_id}: "
                    f"{project_path}"
                )
            return project_path
        return None


    def _capture_logs(self) -> list[OrchestratorDecision]:
        """Capture agent output from all roles for debugging + stuck detection.

        G-RESUME-4 also runs the is_alive watchdog here: each failed
        check bumps a per-instance counter; reaching _dead_threshold
        triggers a respawn via SpawnCoordinator.
        """
        decisions: list[OrchestratorDecision] = []
        logs_dir = self.state_dir / "logs"
        logs_dir.mkdir(parents=True, exist_ok=True)
        # 2026-06-10 review P1-3: after a safe-halt the watchdog must stop
        # regenerating respawn attempts; only capture survives for diagnosis.
        safe_halted = self._runtime_safe_halted()

        for role in self.config.roles:
            if role.name == "orchestrator":
                continue
            if self._last_worker_state.get(role.instance_id) == "blocked_human":
                continue
            # G-RESUME-4: liveness check. Both claude and codex are
            # persistent TUI processes in tmux panes — same watchdog
            # path applies to both.
            alive = False
            try:
                alive = self.transport.is_alive(role.instance_id)
            except Exception:
                alive = False
            if not alive and safe_halted:
                continue  # halted: no counter bump, no respawn churn
            if not alive:
                # I41 demotion (doc 87 §6 rev4, backlog 2026-06-11-0325):
                # the pane probe alone is EVIDENCE, never a control signal.
                # The probe only prefilters WHEN to evaluate; the respawn
                # decision condition is kernel-state derived — an active
                # task whose worker shows no recent liveness evidence
                # (heartbeat in role_sessions.yaml, else latest events.jsonl
                # activity). A completed/idle worker with a closed pane no
                # longer gets respawned (the R18 falsepos class), and a
                # busy worker with a fresh heartbeat survives probe flaps.
                self._dead_counter[role.instance_id] = (
                    self._dead_counter.get(role.instance_id, 0) + 1
                )
                if (
                    self._dead_counter[role.instance_id]
                    >= self._dead_threshold
                ):
                    self._dead_counter[role.instance_id] = 0
                    active_task = self._active_task_for_instance(role.instance_id)
                    observed = getattr(self, "_pane_dead_observed", None)
                    if observed is None:
                        observed = set()
                        self._pane_dead_observed = observed
                    if role.instance_id not in observed:
                        observed.add(role.instance_id)
                        try:
                            self.event_writer.append(ZfEvent(
                                type="worker.pane.dead_observed",
                                actor=role.instance_id,
                                task_id=active_task.id if active_task else None,
                                payload={
                                    "instance_id": role.instance_id,
                                    "role": role.name,
                                    "consecutive_probes": self._dead_threshold,
                                    "source": "dead_watchdog",
                                    "note": (
                                        "evidence (I41): probe prefilter; "
                                        "respawn requires kernel-state "
                                        "staleness corroboration"
                                    ),
                                },
                            ))
                        except Exception:
                            pass
                    stale, stale_basis = self._worker_liveness_stale(role)
                    if stale:
                        # Pending-obligation recovery first: a dead worker
                        # whose manifest terminal is still pending gets the
                        # terminal-completion request regardless of kanban
                        # status (the reactor may already have advanced the
                        # task off in_progress when it consumed the
                        # manifest event).
                        manifest_recovery = (
                            self._request_manifest_terminal_completion_if_pending(
                                role=role,
                                task=active_task,
                                reason="dead_watchdog",
                                inject_prompt=False,
                            )
                        )
                        if manifest_recovery is not None:
                            decisions.append(manifest_recovery)
                            continue
                        if active_task is not None:
                            self._emit_worker_runner_failed(
                                role=role,
                                task=active_task,
                                source="dead_watchdog",
                                reason=(
                                    "pane dead and no recent liveness "
                                    f"evidence ({stale_basis})"
                                ),
                            )
                            decisions.append(self._respawn_instance(role))
                        # else: idle worker with no pending obligations —
                        # evidence only (the R18 completed-worker falsepos
                        # class); recovery happens at next dispatch/stuck.
                continue  # skip capture for dead pane
            else:
                self._dead_counter[role.instance_id] = 0
                observed = getattr(self, "_pane_dead_observed", None)
                if observed is not None:
                    observed.discard(role.instance_id)

            try:
                output = self.transport.capture_log(role.instance_id, lines=200)
            except Exception:
                continue  # pane might not exist yet
            if output.strip():
                log_path = logs_dir / f"{role.instance_id}.log"
                log_path.write_text(output)
                fingerprints = getattr(self, "_worker_log_fingerprints", None)
                if fingerprints is None:
                    fingerprints = {}
                    self._worker_log_fingerprints = fingerprints
                activity = getattr(self, "_worker_activity_epoch", None)
                if activity is None:
                    activity = {}
                    self._worker_activity_epoch = activity
                fingerprint = hashlib.sha256(
                    output.encode("utf-8", errors="replace"),
                ).hexdigest()
                if fingerprints.get(role.instance_id) != fingerprint:
                    fingerprints[role.instance_id] = fingerprint
                    activity[role.instance_id] = self._now()
                    self._stuck_already_reported.discard(role.instance_id)

            # G-LIFE-3: feed output into the per-instance stuck detector.
            detector = self._stuck_detectors.get(role.instance_id)
            if detector is None or not output.strip():
                continue
            # Idle workers legitimately have unchanged pane output. A stale
            # task.dispatched entry can still consume WIP after its fanout
            # child is terminal, so only a canonical active task or live
            # fanout child counts as an outstanding obligation here.
            active_task = self._active_task_for_instance(role.instance_id)
            active_fanout_child = self._active_fanout_child_for_instance(
                role.instance_id,
            )
            if active_task is None and active_fanout_child is None:
                detector.reset()
                self._stuck_already_reported.discard(role.instance_id)
                continue
            try:
                latest_dispatched = self._latest_dispatched_per_task()
            except Exception:
                latest_dispatched = {}
            is_idle = self.wip.can_accept(
                role.instance_id,
                self.task_store,
                latest_dispatched,
            )
            if is_idle and active_task is not None:
                is_idle = False
            if is_idle:
                detector.reset()
                self._stuck_already_reported.discard(role.instance_id)
                continue
            if active_task is not None:
                dispatch = self._latest_dispatch_event_for_task(active_task.id)
                dispatch_payload = dispatch.payload if dispatch is not None else {}
                dispatch_id = ""
                if isinstance(dispatch_payload, dict):
                    dispatch_id = str(
                        dispatch_payload.get("dispatch_id")
                        or active_task.active_dispatch_id
                        or ""
                    )
                progress_event = self._latest_unrejected_progress_event_for_dispatch(
                    active_task.id,
                    dispatch_id,
                )
                if progress_event is not None:
                    detector.reset()
                    self._stuck_already_reported.discard(role.instance_id)
                    self._set_worker_state(
                        role.instance_id,
                        self._worker_state_after_progress_event(progress_event),
                        reason=(
                            f"{progress_event.type} already recorded for task "
                            f"{active_task.id}; skipping stuck detector"
                        ),
                        task_id=active_task.id,
                    )
                    continue
            detector.update(output)
            if (
                detector.is_stuck()
                and role.instance_id not in self._stuck_already_reported
            ):
                if self._provider_stuck_grace_active(role, active_task):
                    self._set_worker_state(
                        role.instance_id,
                        "busy",
                        reason=(
                            "codex active turn is within provider stuck grace; "
                            "suppressing pane-output stuck detector"
                        ),
                        task_id=active_task.id,
                    )
                    continue
                self._stuck_already_reported.add(role.instance_id)
                decisions.append(self._report_stuck_worker(role))

        return decisions

    def _emit_worker_runner_failed(
        self,
        *,
        role: "RoleConfig",
        task: Task | None,
        source: str,
        reason: str,
    ) -> None:
        """Record deterministic runner/process failure observed by Layer 1."""
        lifecycle: dict[str, object] = {
            "role_name": role.instance_id,
            "alive": False,
            "pane_pid": "",
            "current_command": "",
            "current_path": "",
        }
        snapshot_fn = getattr(self.transport, "lifecycle_snapshot", None)
        if snapshot_fn is not None:
            try:
                snapshot = snapshot_fn(role.instance_id)
                to_payload = getattr(snapshot, "to_payload", None)
                if to_payload is not None:
                    raw = to_payload()
                    if isinstance(raw, dict):
                        lifecycle.update(raw)
            except Exception:
                pass

        dispatch_id = ""
        if task is not None:
            dispatch_id = getattr(task, "active_dispatch_id", "") or ""
        try:
            self.event_writer.append(ZfEvent(
                type="worker.runner.failed",
                actor=role.instance_id,
                task_id=task.id if task is not None else None,
                payload={
                    "role": role.name,
                    "instance_id": role.instance_id,
                    "backend": role.backend,
                    "task_id": task.id if task is not None else "",
                    "dispatch_id": dispatch_id,
                    "source": source,
                    "reason": reason,
                    "dead_threshold": self._dead_threshold,
                    "lifecycle": lifecycle,
                },
            ))
        except Exception:
            pass

    def _apply_repair_action_request(self, event: ZfEvent) -> None:
        from zf.runtime.repair_action_executor import RepairActionExecutor

        RepairActionExecutor(
            event_log=self.event_log,
            task_store=self.task_store,
            event_writer=self.event_writer,
            roles=self.config.roles,
            respawn_worker=self._respawn_instance,
            cancel_worker=self._cancel_instance,
            rerun_fanout_child=self._rerun_fanout_child,
        ).apply(event)


    # Respawn retry cap (per backlog 2026-05-14-1439). Mirrors the
    # dispatch retry cap pattern (2026-05-14-1311). Without this, a
    # worker that fails to come up after spawn (e.g. claude pane stuck
    # mid-token, slow startup) triggers an indefinite respawn loop —
    # cangjie r3 observed 11 worker.respawn.failed in a single run.
    #
    # 2026-06-10 review P0-1: the cap counts CONSECUTIVE failures and is
    # reset only by a successful respawn (_clear_respawn_failure), never
    # by elapsed time. The previous 120s sliding window was smaller than
    # the real watchdog retry cadence (~330s in cangjie), so the counter
    # reset to 1 on every failure and the entire cooldown/cascade/
    # safe-halt path was dead code — 786 worker.respawn.failed over 7.1h
    # with zero cooldown events.
    _RESPAWN_FAILURE_MAX_CONSECUTIVE = 3
    _RESPAWN_FAILURE_BACKOFF_SECONDS = 300.0
    _RESPAWN_SUCCESS_WINDOW_SECONDS = 1200.0
    _RESPAWN_SUCCESS_MAX_PER_WINDOW = 3

    def _record_respawn_failure(self, instance_id: str) -> None:
        registry = getattr(self, "_respawn_failure_registry", None)
        if registry is None:
            registry = {}
            self._respawn_failure_registry = registry
        now = self._now() if hasattr(self, "_now") else 0.0
        count = self._consecutive_respawn_failures(instance_id)
        entry = registry.get(instance_id)
        cooldown_until = entry[2] if entry else 0.0
        if cooldown_until and now < cooldown_until:
            # Cap already fired for this exhaustion episode; the parked /
            # cooled-down state owns it — no duplicate cascade storm.
            registry[instance_id] = (count, now, cooldown_until)
            return
        if count >= self._RESPAWN_FAILURE_MAX_CONSECUTIVE:
            cooldown_until = now + self._RESPAWN_FAILURE_BACKOFF_SECONDS
            try:
                self.event_writer.append(ZfEvent(
                    type="worker.respawn.cooldown",
                    actor=instance_id,
                    payload={
                        "instance_id": instance_id,
                        "consecutive_failures": count,
                        "cooldown_seconds": self._RESPAWN_FAILURE_BACKOFF_SECONDS,
                    },
                ))
            except Exception:
                pass
            # Park the worker in blocked_human so operator sees + watchdog
            # stops retrying without an explicit clear from the operator.
            try:
                self._set_worker_state(
                    instance_id,
                    "blocked_human",
                    reason="respawn cap exhausted; operator escalation",
                )
            except Exception:
                pass
            # doc 79 Tier1 no-dead-end: parking in blocked_human is the R12
            # limbo when unattended (operator never comes → 6h空转). Route the
            # exhausted infra retry through the cascade so it lands on an
            # explicit escalate (operator reachable) or safe-halt (not) signal
            # instead of silently waiting forever.
            self._emit_respawn_cascade(instance_id, attempts=count)
        registry[instance_id] = (count, now, cooldown_until)

    def _emit_respawn_cascade(self, instance_id: str, *, attempts: int) -> None:
        """Emit the no-dead-end cascade decision for an exhausted respawn.

        worker_stuck is infra/retryable; once the bounded respawn retries are
        spent, the cascade decides escalate vs safe-halt rather than leaving
        the worker dead-ended in blocked_human limbo.
        """
        decision = decide_cascade(
            failure_class="worker_stuck",
            attempts=attempts,
            cap=self._RESPAWN_FAILURE_MAX_CONSECUTIVE,
            liveness=self._operator_channel_live(),
        )
        try:
            cascade_ev = self.event_writer.append(ZfEvent(
                type="remediation.cascade",
                actor=instance_id,
                payload={
                    "instance_id": instance_id,
                    "failure_class": decision.failure_class,
                    "bucket": decision.bucket,
                    "tier": decision.tier,
                    "reason": decision.reason,
                    "attempts": attempts,
                    "cap": self._RESPAWN_FAILURE_MAX_CONSECUTIVE,
                    "safe_halt": decision.tier == CASCADE_SAFE_HALT,
                },
            ))
            if decision.tier == CASCADE_SAFE_HALT:
                # evidence = the cascade event itself (carries class/attempts/
                # reason) so the safe_halted signal points at why it halted.
                self._execute_safe_halt(
                    instance_id, decision,
                    evidence_event_ids=[getattr(cascade_ev, "id", "")],
                )
        except Exception:
            pass

    def _execute_safe_halt(
        self, instance_id: str, decision, *, evidence_event_ids=None,
    ) -> None:
        """doc 79 §4.4 floor: actually stop, don't just signal.

        Emit the single terminal ``runtime.safe_halted`` (root class + reason)
        AND pause dispatch via the existing enforced gate (``dispatch.paused``
        → ``is_dispatch_paused``) so the run stops accumulating stuck/escalate
        instead of a 287th escalate into the void. Unconditional — not gated by
        ZF_AUTORESEARCH_AUTO_REPAIR.
        """
        self.event_writer.append(ZfEvent(
            type=SAFE_HALTED_EVENT,
            actor=instance_id,
            payload=build_safe_halt_payload(
                root_failure_class=decision.failure_class,
                evidence_event_ids=[e for e in (evidence_event_ids or []) if e],
                reason=decision.reason,
            ),
        ))
        self.event_writer.append(ZfEvent(
            type="dispatch.paused",
            actor=instance_id,
            payload={
                "reason": "safe_halt",
                "source": "remediation_cascade",
                "root_failure_class": decision.failure_class,
            },
        ))

    def _clear_respawn_failure(self, instance_id: str) -> None:
        registry = getattr(self, "_respawn_failure_registry", None)
        if registry is not None:
            registry.pop(instance_id, None)

    def _maybe_open_respawn_success_circuit(
        self,
        instance_id: str,
        *,
        task_id: str = "",
    ) -> bool:
        if self._respawn_success_circuit_active(instance_id):
            return True
        recent = self._recent_respawn_success_events(instance_id)
        if len(recent) < self._RESPAWN_SUCCESS_MAX_PER_WINDOW:
            return False
        opened = getattr(self, "_respawn_success_circuit_opened", None)
        if opened is None:
            opened = set()
            self._respawn_success_circuit_opened = opened
        opened.add(instance_id)
        evidence_event_ids = [event.id for event in recent[-self._RESPAWN_SUCCESS_MAX_PER_WINDOW:]]
        fingerprint = f"respawn_success_cascade:{instance_id}"
        circuit_event = self.event_writer.append(ZfEvent(
            type="worker.respawn.circuit_opened",
            actor=instance_id,
            task_id=task_id or None,
            payload={
                "instance_id": instance_id,
                "task_id": task_id,
                "successes_in_window": len(recent),
                "cap": self._RESPAWN_SUCCESS_MAX_PER_WINDOW,
                "window_seconds": self._RESPAWN_SUCCESS_WINDOW_SECONDS,
                "fingerprint": fingerprint,
                "evidence_event_ids": evidence_event_ids,
                "reason": "worker respawned repeatedly without durable recovery",
            },
        ))
        try:
            self._set_worker_state(
                instance_id,
                "blocked_human",
                reason="respawn success circuit opened; recovery required",
                task_id=task_id,
                force=True,
            )
        except Exception:
            pass
        try:
            self.event_writer.append(ZfEvent(
                type="runtime.attention.needed",
                actor=instance_id,
                task_id=task_id or None,
                payload={
                    "severity": "critical",
                    "reason": "respawn_success_circuit_opened",
                    "fingerprint": fingerprint,
                    "evidence_event_ids": evidence_event_ids,
                },
                causation_id=circuit_event.id,
            ))
        except Exception:
            pass
        try:
            self.event_writer.append(ZfEvent(
                type="autoresearch.invocation.requested",
                actor="zf-cli",
                task_id=task_id or None,
                payload={
                    "invocation_id": f"arinv-{fingerprint}",
                    "level": "diagnose",
                    "apply_policy": "proposal_only",
                    "severity": "critical",
                    "trigger_reason": "respawn success circuit opened",
                    "fingerprint": fingerprint,
                    "evidence_event_ids": evidence_event_ids,
                    "source_event_id": circuit_event.id,
                },
                causation_id=circuit_event.id,
            ))
        except Exception:
            pass
        return True

    def _cancel_instance(self, role: "RoleConfig") -> "OrchestratorDecision":
        active_task = self._active_task_for_instance(role.instance_id)
        active_task_id = active_task.id if active_task is not None else ""
        self._set_worker_state(
            role.instance_id,
            "cancelling",
            reason="repair action cancellation requested",
            task_id=active_task_id,
            force=True,
        )
        try:
            self.transport.terminate(role.instance_id)
        except Exception as exc:
            self._set_worker_state(
                role.instance_id,
                "blocked_human",
                reason=f"repair action cancel failed: {exc}",
                task_id=active_task_id,
                force=True,
            )
            return OrchestratorDecision(
                action="cancel_failed",
                role=role.instance_id,
                task_id=active_task_id,
                reason=f"worker cancel failed: {exc}",
            )
        self._set_worker_state(
            role.instance_id,
            "blocked_human",
            reason="repair action cancelled worker",
            task_id=active_task_id,
            force=True,
        )
        return OrchestratorDecision(
            action="cancel",
            role=role.instance_id,
            task_id=active_task_id,
            reason="worker cancelled by repair action",
        )

    def _rerun_fanout_child(
        self,
        fanout_id: str,
        child_id: str,
    ) -> "OrchestratorDecision":
        manifest = self._fanout_manifest(fanout_id)
        if not manifest:
            return OrchestratorDecision(
                action="rerun_fanout_child_failed",
                reason=f"unknown_fanout:{fanout_id}",
            )
        child = self._fanout_child(manifest, child_id)
        if child is None:
            return OrchestratorDecision(
                action="rerun_fanout_child_failed",
                reason=f"unknown_fanout_child:{child_id}",
            )
        role_instance = str(child.get("role_instance") or "")
        task_id = str(child.get("task_id") or "")
        status = str(child.get("status") or "")
        if status in {"completed", "failed"}:
            return OrchestratorDecision(
                action="rerun_fanout_child_failed",
                role=role_instance,
                task_id=task_id,
                reason=f"fanout_child_terminal:{status}",
            )
        stale_reason, superseded_by = self._fanout_identity_stale_reason(fanout_id)
        if stale_reason:
            suffix = f":{superseded_by}" if superseded_by else ""
            return OrchestratorDecision(
                action="rerun_fanout_child_failed",
                role=role_instance,
                task_id=task_id,
                reason=f"stale_fanout:{stale_reason}{suffix}",
            )
        role = next(iter(self._fanout_roles([role_instance])), None)
        if role is None:
            return OrchestratorDecision(
                action="rerun_fanout_child_failed",
                role=role_instance,
                task_id=task_id,
                reason=f"unknown_worker:{role_instance or '(missing)'}",
            )
        dispatches: list[ZfEvent] = []
        terminal_event_type = ""
        for event in self.event_log.read_all():
            if event.type not in {
                "fanout.child.dispatched",
                "fanout.child.completed",
                "fanout.child.failed",
            }:
                continue
            payload = event.payload if isinstance(event.payload, dict) else {}
            if str(payload.get("fanout_id") or "") != fanout_id:
                continue
            if str(payload.get("child_id") or "") != child_id:
                continue
            if event.type == "fanout.child.dispatched":
                dispatches.append(event)
            else:
                terminal_event_type = event.type
        if terminal_event_type:
            return OrchestratorDecision(
                action="rerun_fanout_child_failed",
                role=role.instance_id,
                task_id=task_id,
                reason=f"fanout_child_terminal:{terminal_event_type}",
            )
        if not dispatches:
            return OrchestratorDecision(
                action="rerun_fanout_child_failed",
                role=role.instance_id,
                task_id=task_id,
                reason="missing_previous_fanout_child_dispatch",
            )
        self._retry_fanout_child(
            manifest=manifest,
            child=child,
            previous_dispatch=dispatches[-1],
            attempt=len(dispatches),
        )
        return OrchestratorDecision(
            action="rerun_fanout_child",
            role=role.instance_id,
            task_id=task_id,
            reason="fanout child rerun dispatched",
        )

    def restart_role_instance(self, role: "RoleConfig") -> "OrchestratorDecision":
        """Restart one role through the same recovery path as the watchdog.

        A CLI/operator restart is still a provider-session replacement.  It
        must therefore preserve the active task or fanout-child contract
        instead of merely opening a fresh interactive pane.
        """
        return self._respawn_instance(role, recovery_reason="manual_restart")

    def _respawn_instance(
        self,
        role: "RoleConfig",
        *,
        recovery_reason: str = "watchdog",
    ) -> "OrchestratorDecision":
        """Serialize replacement of one provider session across processes.

        ``zf restart <role>`` creates a short-lived Orchestrator while the
        resident watcher may concurrently detect the same dead pane.  Without
        a state-dir-scoped lease, both processes can spawn a replacement and
        leave one unbound pane running.  The lease is deliberately per role:
        unrelated lanes remain independently recoverable.
        """
        active_task = self._active_task_for_instance(role.instance_id)
        lock_dir = self.state_dir / "locks" / "respawns"
        try:
            with SessionLock(lock_dir, role.instance_id):
                return self._respawn_instance_with_lease(
                    role,
                    recovery_reason=recovery_reason,
                )
        except SessionLockBusy:
            try:
                self.event_writer.append(ZfEvent(
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
            return OrchestratorDecision(
                action="respawn_in_progress",
                role=role.instance_id,
                task_id=active_task.id if active_task is not None else "",
                reason="worker respawn already in progress for this role",
            )

    def _respawn_instance_with_lease(
        self,
        role: "RoleConfig",
        *,
        recovery_reason: str = "watchdog",
    ) -> "OrchestratorDecision":
        """G-RESUME-4/5: watchdog-triggered respawn.

        Uses SpawnCoordinator so the correct --resume / exec resume
        semantics fire (the instance is already marked spawned, so
        SpawnCoordinator.spawn treats this as a restart). After the
        respawn completes, build_recovery_briefing is assembled and
        injected as the first send_task so the agent reloads memory /
        task / progress / git state / causal chain.
        """
        # Retry cap (backlog 2026-05-14-1439): refuse if this instance
        # has flooded recent respawn failures. Park in blocked_human
        # state until operator clears.
        if self._respawn_success_circuit_active(role.instance_id):
            return OrchestratorDecision(
                action="respawn_circuit_open",
                role=role.instance_id,
                reason=(
                    "worker respawn success circuit open; recovery required"
                ),
            )
        if self._respawn_recent_failure_cooldown_active(role.instance_id):
            return OrchestratorDecision(
                action="respawn_cooldown",
                role=role.instance_id,
                reason=(
                    "worker.respawn cooldown active; operator escalation required"
                ),
            )
        # B3: watchdog has tripped — worker is currently stuck/dead and
        # we're now starting the respawn flow.
        self._set_worker_state(
            role.instance_id, "respawning",
            reason=(
                "watchdog is_alive=False"
                if recovery_reason == "watchdog"
                else "operator requested role restart"
            ),
        )
        coordinator = self._get_spawn_coordinator()
        try:
            if role.backend == "codex" and self._codex_context_exhausted(role):
                reg = RoleSessionRegistry(
                    self.state_dir / "role_sessions.yaml",
                    project_root=str(self.project_root),
                )
                old_session = reg.get(role.instance_id)
                reg.clear(role.instance_id)
                active_task = self._active_task_for_instance(role.instance_id)
                dispatch_id = ""
                if active_task is not None:
                    dispatch_id = getattr(active_task, "active_dispatch_id", "") or ""
                critical = self.event_writer.append(ZfEvent(
                    type="worker.context.critical",
                    actor=role.instance_id,
                    task_id=active_task.id if active_task is not None else None,
                    payload={
                        "task_id": active_task.id if active_task is not None else "",
                        "dispatch_id": dispatch_id,
                        "role": role.name,
                        "instance_id": role.instance_id,
                        "backend": role.backend,
                        "context_usage_ratio": None,
                        "session_ref": str(old_session) if old_session else "",
                        "source": "provider_context_detector",
                        "reason": "provider_context_window_exhausted",
                        "action": "clear_codex_session_before_respawn",
                        "old_session": str(old_session) if old_session else "",
                    },
                ))
                self._route_context_critical_inline(critical)
            # Terminate any lingering pane/window state
            try:
                self.transport.terminate(role.instance_id)
            except Exception:
                pass
            coordinator.spawn(
                role,
                cwd=self._role_spawn_cwd(
                    role,
                    source=f"{recovery_reason}_respawn",
                ),
            )
            # G-RESUME-5: inject recovery briefing as first user message
            if not self._wait_role_ready(role):
                # R17/R18: claude `--resume <uuid>` can fail "session already in
                # use" when the old session lock survives the pane termination, so
                # the resume respawn never becomes ready and the infra cascade
                # safe-halts the run. Fall back to a FRESH session ONCE (clear →
                # `--session-id <new>`), keeping --resume the default so normal
                # crash recovery still preserves session context.
                if role.backend == "claude-code":
                    try:
                        RoleSessionRegistry(
                            self.state_dir / "role_sessions.yaml",
                            project_root=str(self.project_root),
                        ).clear(role.instance_id)
                    except Exception:
                        pass
                    coordinator.spawn(
                        role,
                        cwd=self._role_spawn_cwd(
                            role,
                            source=f"{recovery_reason}_respawn_fresh",
                        ),
                    )
                if not self._wait_role_ready(role):
                    raise RuntimeError(
                        f"worker {role.instance_id} did not become ready after spawn"
                    )
            active_fanout = self._active_fanout_child_for_instance(
                role.instance_id,
            )
            if active_fanout is not None:
                recovered = self._inject_fanout_recovery_briefing(
                    role,
                    active_fanout,
                    recovery_reason=f"active_fanout_after_{recovery_reason}",
                )
            else:
                recovered = self._inject_recovery_briefing(role)
            if not recovered:
                raise RuntimeError(
                    f"failed to inject recovery briefing for {role.instance_id}"
                )
            self.event_writer.append(ZfEvent(
                type="worker.respawned",
                actor=role.instance_id,
                payload={
                    "role": role.name,
                    "instance_id": role.instance_id,
                    "reason": recovery_reason,
                },
            ))
            active_task = self._active_task_for_instance(role.instance_id)
            active_task_id = (
                str(active_fanout.get("task_id") or "")
                if active_fanout is not None
                else active_task.id if active_task is not None else ""
            )
            if self._maybe_open_respawn_success_circuit(
                role.instance_id,
                task_id=active_task_id,
            ):
                return OrchestratorDecision(
                    action="respawn_circuit_open",
                    role=role.instance_id,
                    task_id=active_task_id,
                    reason=(
                        "worker respawned repeatedly without durable recovery"
                    ),
                )
            # A recovered active task remains busy; an idle respawn clears any
            # stale generation left by the terminated provider process.
            self._set_worker_state(
                role.instance_id,
                "busy" if active_task is not None or active_fanout is not None else "idle",
                reason="respawn complete",
                task_id=active_task_id,
                force=active_task is None and active_fanout is None,
            )
            self._clear_respawn_failure(role.instance_id)
            return OrchestratorDecision(
                action="respawn",
                role=role.instance_id,
                task_id=active_task_id,
                reason=f"worker.respawned ({recovery_reason})",
            )
        except Exception as e:
            # Respawn failed — emit diagnostic, record failure (cap)
            # and keep going.
            try:
                self.event_writer.append(ZfEvent(
                    type="worker.respawn.failed",
                    actor=role.instance_id,
                    payload={"role": role.name, "error": str(e)},
                ))
            except Exception:
                pass
            self._record_respawn_failure(role.instance_id)
            return OrchestratorDecision(
                action="respawn_failed",
                role=role.instance_id,
                reason=f"watchdog respawn failed: {e}",
            )

    def _codex_context_exhausted(self, role: "RoleConfig") -> bool:
        try:
            output = self.transport.capture_log(role.instance_id, lines=120)
        except Exception:
            return False
        return has_provider_context_exhausted(output)

    # -- G-WIRE-3: refresh policy observation layer --


    def _route_context_critical_inline(self, event: ZfEvent) -> None:
        handler = getattr(self, "_on_context_critical", None)
        if not callable(handler):
            return
        try:
            decision = handler(event)
            if event.task_id or decision is not None:
                self._processed_event_ids.add(event.id)
        except Exception:
            pass


    def _rescue_orphan_pending_recycles(self) -> None:
        """r6-F6:回收任务重启即丢 → pending_recycle 孤儿救援。

        force-recycle 依赖内存态(_hard_cap_exceeded/_instance_state),
        重启后清零;worker 停在 pending_recycle,回收无人执行,该实例的
        dispatch 永久 deferred(r6 实弹:verify-scene 卡 40 分钟,全靠
        operator 杀 pane)。sweep 从 role_sessions 心跳真相接管:state
        为 pending_recycle 且心跳静止超过 600s 且本进程未在跟踪 →
        强制进入 recycling 并启动回收。
        """
        try:
            from datetime import datetime, timezone

            from zf.core.state.role_sessions import RoleSessionRegistry

            registry = RoleSessionRegistry(
                self.state_dir / "role_sessions.yaml",
                project_root=str(self.project_root),
            )
        except Exception:
            return
        for role in getattr(self.config, "roles", []) or []:
            instance_id = getattr(role, "instance_id", "") or getattr(role, "name", "")
            if not instance_id:
                continue
            if self._instance_state.get(instance_id) in ("pending_recycle", "recycling"):
                continue  # 本进程已在管
            try:
                heartbeat_ts, heartbeat = registry.get_last_heartbeat(instance_id)
            except Exception:
                continue
            state = str((heartbeat or {}).get("state") or "")
            if state != "pending_recycle":
                continue
            age = None
            try:
                parsed = datetime.fromisoformat(str(heartbeat_ts).replace("Z", "+00:00"))
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=timezone.utc)
                age = (datetime.now(timezone.utc) - parsed).total_seconds()
            except Exception:
                continue
            if age is None or age < 600.0:
                continue
            self._instance_state[instance_id] = "recycling"
            self._set_worker_state(
                instance_id, "recycling",
                reason=(
                    f"orphan pending_recycle rescued after {age:.0f}s silence "
                    "(recycle executor lost across restart, r6-F6)"
                ),
            )
            try:
                self._start_recycle(role)
            except Exception:
                pass

    def _check_context_thresholds(self) -> None:
        """Poll every role's session file.

        Two responsibilities:
          1. Synthesize agent.usage → cost tracker (all backends, incl.
             orchestrator — Layer 2 is the most expensive role; blind-
             spot until E1 fix).
          2. Recycle decisions: skipped for orchestrator (can't hot-swap
             Layer 2 mid-flight) and for mock/python backends.
        """
        self._rescue_orphan_pending_recycles()
        for role in self.config.roles:
            reader = self._session_readers.get(role.backend)
            if reader is None:
                continue  # mock / python backends
            cached_uuid = None
            cached_path = None
            try:
                from zf.core.state.role_sessions import RoleSessionRegistry
                reg = RoleSessionRegistry(
                    self.state_dir / "role_sessions.yaml",
                    project_root=str(self.project_root),
                )
                cached_uuid = reg.get(role.instance_id)
                cached_path = reg.get_path(role.instance_id)
            except Exception:
                pass
            session_id_str = str(cached_uuid) if cached_uuid else ""
            if role.backend == "codex" and not session_id_str and cached_path is None:
                continue
            # claude-code workers run in per-role worktrees; their session
            # lives under the WORKTREE cwd, not the repo root, so resolve the
            # path from the worktree. (Codex sessions are date-bucketed and
            # cwd-independent, which is why only claude-code was untracked.)
            usage_cwd = str(self.project_root)
            wk = self.config.runtime.workdirs
            if (
                role.backend == "claude-code"
                and getattr(wk, "enabled", False)
                and getattr(wk, "mode", "") == "worktree"
            ):
                wt = self.state_dir / "workdirs" / role.instance_id / "project"
                if wt.exists():
                    usage_cwd = str(wt)
            try:
                path = reader.session_path(  # type: ignore[attr-defined]
                    usage_cwd,
                    session_id_str,
                    cached_path=cached_path,
                )
            except Exception:
                path = None
            if path is None:
                # B-COST-02: session file not found by derived path nor uuid
                # glob → signal (debounced) instead of silently skipping, so a
                # claude-code worker's 0 usage doesn't read as "free".
                self._note_usage_capture_miss(role, usage_cwd, session_id_str)
                continue
            # Captured → clear any pending miss streak for this instance.
            self._usage_capture_misses.pop(role.instance_id, None)
            try:
                usage = reader.read_latest_usage(  # type: ignore[attr-defined]
                    path,
                    fallback_window=role.context_window_tokens,
                )
            except Exception:
                continue
            if usage is None:
                continue
            # G-RECYCLE-8: synthesize agent.usage event so tmux-hosted
            # workers feed the cost tracker. Dedup via (instance, ts).
            sample_is_new = self._synthesize_agent_usage(role, usage)
            # E1 fix: orchestrator gets cost tracking (above) but no
            # recycle — Layer 2 state is in its Claude session, hot-swap
            # would lose the orchestration train of thought.
            if role.name == "orchestrator":
                continue
            # LH-0.T4 / doc 59: warning, compact, and hard-cap are
            # distinct. Warning checkpoints recovery facts; compact and
            # recycle are held until the compact threshold.
            # Drives _find_available_role skip + force-recycle path.
            if usage.ratio >= role.context_hard_cap:
                if role.instance_id not in self._hard_cap_exceeded:
                    self._hard_cap_exceeded[role.instance_id] = self._now()
            elif usage.ratio < role.context_warning_threshold:
                self._hard_cap_exceeded.pop(role.instance_id, None)
            if usage.ratio < role.context_compact_threshold:
                self._context_compact_attempts().discard(role.instance_id)
            if usage.ratio < role.context_warning_threshold:
                continue
            # Already tracked? Don't double-trigger pending/recycling.
            # "healthy" (explicit, after previous recycle completed)
            # allows re-trigger if bloat recurs.
            current = self._instance_state.get(role.instance_id, "healthy")
            if not sample_is_new and current not in {"pending_recycle", "recycling"}:
                continue
            # LH-0.T4: force-recycle path bypasses pending_recycle guard.
            # If hard cap has been over drain_hold_seconds, escalate
            # pending_recycle → recycling (force), no matter busy/idle.
            over_hard_cap = self._hard_cap_exceeded.get(role.instance_id)
            force_recycle = (
                over_hard_cap is not None
                and (self._now() - over_hard_cap) >= role.drain_hold_seconds
            )
            if force_recycle and current != "recycling":
                self._instance_state[role.instance_id] = "recycling"
                self._set_worker_state(
                    role.instance_id, "recycling",
                    reason=(
                        f"context ratio {usage.ratio:.2f} "
                        f"over hard cap {role.context_hard_cap} "
                        f"for >= {role.drain_hold_seconds:.0f}s, force recycle"
                    ),
                )
                self._start_recycle(role)
                continue
            if current in ("pending_recycle", "recycling"):
                continue
            # Emit warning (busy or idle). In multi-replica mode
            # task.assigned_to may be the role name ("dev") while the
            # actual in-flight worker is a concrete instance ("dev-1").
            # Lifecycle decisions must use the dispatch event truth.
            try:
                latest_dispatched = self._latest_dispatched_per_task()
            except Exception:
                latest_dispatched = {}
            is_idle = self.wip.can_accept(
                role.instance_id,
                self.task_store,
                latest_dispatched,
            )
            active_fanout = self._active_fanout_child_for_instance(
                role.instance_id
            )
            if is_idle and (
                self._active_task_for_instance(role.instance_id)
                or active_fanout is not None
            ):
                is_idle = False
            warning_payload, active_task = self._context_threshold_payload(
                role,
                usage,
                session_id=session_id_str,
                cached_path=cached_path,
                idle=is_idle,
                reason="recycle_threshold_exceeded",
            )
            if active_fanout is not None:
                warning_payload["active_fanout"] = {
                    "fanout_id": str(active_fanout.get("fanout_id") or ""),
                    "stage_id": str(active_fanout.get("stage_id") or ""),
                    "child_id": str(active_fanout.get("child_id") or ""),
                    "run_id": str(active_fanout.get("run_id") or ""),
                }
            self.event_writer.append(ZfEvent(
                type="worker.context.warning",
                actor=role.instance_id,
                task_id=active_task.id if active_task is not None else None,
                payload=warning_payload,
            ))
            # Hard cap? Extra critical event.
            if usage.ratio >= role.context_hard_cap:
                critical_payload = dict(warning_payload)
                critical_payload.update({
                    "reason": "hard_cap_exceeded",
                    "hard_cap": role.context_hard_cap,
                })
                critical = self.event_writer.append(ZfEvent(
                    type="worker.context.critical",
                    actor=role.instance_id,
                    task_id=active_task.id if active_task is not None else None,
                    payload=critical_payload,
                ))
                self._route_context_critical_inline(critical)
            if usage.ratio < role.context_compact_threshold:
                continue
            if (
                active_task is not None
                and usage.ratio < role.context_hard_cap
                and self._start_compact_first(role, warning_payload, active_task)
            ):
                continue
            if is_idle:
                self._instance_state[role.instance_id] = "recycling"
                self._set_worker_state(
                    role.instance_id, "recycling",
                    reason=f"context ratio {usage.ratio:.2f}, idle",
                )
                self._start_recycle(role)
            else:
                self._instance_state[role.instance_id] = "pending_recycle"
                self._set_worker_state(
                    role.instance_id, "pending_recycle",
                    reason=f"context ratio {usage.ratio:.2f}, busy",
                )


    def _start_compact_first(
        self,
        role: "RoleConfig",
        warning_payload: dict,
        active_task: Task,
    ) -> bool:
        attempts = self._context_compact_attempts()
        if role.instance_id in attempts:
            return False
        try:
            from zf.runtime.backend import get_adapter

            caps = get_adapter(role.backend).capabilities
        except Exception:
            return False
        command = str(getattr(caps, "compact_command", "") or "").strip()
        if not getattr(caps, "native_compact", False) or not command:
            return False
        attempts.add(role.instance_id)
        payload = dict(warning_payload)
        payload.update({
            "compact_command": command,
            "strategy": "compact_first",
        })
        if bool(getattr(caps, "compact_requires_idle", False)):
            self._emit_compact_failed(
                role,
                active_task,
                payload,
                reason="backend compact requires idle session",
                causation_id=None,
                correlation_id=None,
            )
            return False
        requested = self.event_writer.append(ZfEvent(
            type="worker.context.compact.requested",
            actor=role.instance_id,
            task_id=active_task.id,
            payload=payload,
        ))
        try:
            started = bool(self.transport.compact_context(role.instance_id, command))
        except Exception as exc:
            self._emit_compact_failed(
                role,
                active_task,
                payload,
                reason=str(exc),
                causation_id=requested.id,
                correlation_id=requested.correlation_id,
            )
            return False
        if not started:
            self._emit_compact_failed(
                role,
                active_task,
                payload,
                reason="transport did not accept compact command",
                causation_id=requested.id,
                correlation_id=requested.correlation_id,
            )
            return False
        denial = self._compact_denial_reason(role)
        if denial:
            self._emit_compact_failed(
                role,
                active_task,
                payload,
                reason=denial,
                causation_id=requested.id,
                correlation_id=requested.correlation_id,
            )
            return False
        self.event_writer.append(ZfEvent(
            type="worker.context.compacted",
            actor=role.instance_id,
            task_id=active_task.id,
            payload=payload,
            causation_id=requested.id,
            correlation_id=requested.correlation_id,
        ))
        if not self._inject_recovery_briefing(role, inject_idle_prompt=True):
            self._emit_compact_failed(
                role,
                active_task,
                payload,
                reason="recovery briefing injection failed after compact",
                causation_id=requested.id,
                correlation_id=requested.correlation_id,
            )
            self._set_worker_state(
                role.instance_id,
                "blocked",
                reason="compact recovery contract insufficient",
            )
            return True
        self._instance_state[role.instance_id] = "healthy"
        self._set_worker_state(
            role.instance_id,
            "busy",
            reason=f"compact complete; resumed task {active_task.id}",
            task_id=active_task.id,
        )
        return True

    def _emit_compact_failed(
        self,
        role: "RoleConfig",
        active_task: Task,
        warning_payload: dict,
        *,
        reason: str,
        causation_id: str | None,
        correlation_id: str | None,
    ) -> None:
        payload = dict(warning_payload)
        payload.update({
            "strategy": "compact_first",
            "error": reason,
        })
        try:
            self.event_writer.append(ZfEvent(
                type="worker.context.compact.failed",
                actor=role.instance_id,
                task_id=active_task.id,
                payload=payload,
                causation_id=causation_id,
                correlation_id=correlation_id,
            ))
        except Exception:
            pass

    def _provider_stuck_grace_active(
        self,
        role: "RoleConfig",
        active_task: Task | None,
    ) -> bool:
        """Suppress pane-output stuck false positives for active Codex turns.

        Codex can spend many minutes generating with an unchanged TUI pane. A
        pane hash watchdog alone cannot distinguish that from a real stall, so
        keep the existing worker.stuck recovery path but require at least a
        provider floor after the current dispatch before firing it.
        """
        if role.backend != "codex" or active_task is None:
            return False
        dispatch = self._latest_dispatch_event_for_task(active_task.id)
        if dispatch is None:
            return False
        age = self._event_age_seconds(dispatch.ts)
        if age is None:
            return False
        floor = max(
            float(getattr(role, "stuck_threshold_seconds", 0.0) or 0.0),
            self._CODEX_ACTIVE_TURN_STUCK_GRACE_SECONDS,
        )
        return age < floor

    def _check_pending_recycles(self) -> None:
        """Advance pending_recycle → recycling when instance becomes idle."""
        try:
            latest_dispatched = self._latest_dispatched_per_task()
        except Exception:
            latest_dispatched = {}
        for instance_id, state in list(self._instance_state.items()):
            if state != "pending_recycle":
                continue
            is_idle = self.wip.can_accept(
                instance_id,
                self.task_store,
                latest_dispatched,
            )
            if is_idle and (
                self._active_task_for_instance(instance_id)
                or self._active_fanout_child_for_instance(instance_id) is not None
            ):
                is_idle = False
            if not is_idle:
                continue  # still busy
            role = self._find_role_by_instance(instance_id)
            if role is None:
                continue
            self._instance_state[instance_id] = "recycling"
            self._set_worker_state(
                instance_id, "recycling",
                reason="pending recycle drain fired",
            )
            self._start_recycle(role)


    def _start_recycle(self, role: "RoleConfig") -> None:
        """Rotate session_id, respawn with fresh state, inject compact
        recovery briefing. Best-effort: failures leave the instance in
        recycling state so _check_pending doesn't re-trigger."""
        instance_id = role.instance_id
        try:
            active_fanout = self._active_fanout_child_for_instance(instance_id)
            from zf.core.state.role_sessions import RoleSessionRegistry
            reg = RoleSessionRegistry(
                self.state_dir / "role_sessions.yaml",
                project_root=str(self.project_root),
            )
            old_session = reg.get(instance_id)
            if role.backend == "codex":
                # Codex cannot pre-seed a new session UUID; after recycle we
                # must launch fresh and observe the next real rollout file.
                reg.clear(instance_id)
                new_session = None
                session_strategy = "fresh_context_recycle_clear_codex"
            else:
                new_session = reg.rotate(instance_id)
                session_strategy = "fresh_context_recycle_rotated_session"
            self.event_writer.append(ZfEvent(
                type="worker.recycling",
                actor=instance_id,
                payload={
                    "role": role.name,
                    "instance_id": instance_id,
                    "backend": role.backend,
                    "old_session": str(old_session) if old_session else "",
                    "new_session": str(new_session) if new_session else "",
                    "session_strategy": session_strategy,
                },
            ))
            try:
                self.transport.terminate(instance_id)
            except Exception:
                pass
            coordinator = self._get_spawn_coordinator()
            coordinator.spawn(
                role,
                cwd=self._role_spawn_cwd(role, source="context_recycle"),
            )
            # Inject compact recovery briefing
            if not self._wait_role_ready(role):
                raise RuntimeError(
                    f"worker {role.instance_id} did not become ready after recycle"
                )
            if active_fanout is not None:
                recovered = self._inject_fanout_recovery_briefing(
                    role,
                    active_fanout,
                )
            else:
                recovered = self._inject_recovery_briefing(
                    role,
                    inject_idle_prompt=False,
                )
            if not recovered:
                raise RuntimeError(
                    f"failed to inject recovery briefing for {role.instance_id}"
                )
            self.event_writer.append(ZfEvent(
                type="worker.recycled",
                actor=instance_id,
                payload={
                    "role": role.name,
                    "instance_id": instance_id,
                    "backend": role.backend,
                    "new_session": str(new_session) if new_session else "",
                    "session_strategy": session_strategy,
                },
            ))
            # Explicit healthy state (not None) so state machine is
            # inspectable and warnings can re-fire later if bloat recurs.
            self._instance_state[instance_id] = "healthy"
            # B3: recycle complete → idle only when no active task was
            # recovered. Forced recycle on a busy worker injects a recovery
            # briefing and should remain busy from Layer 1's perspective.
            active_task = self._active_task_for_instance(instance_id)
            active_fanout = (
                active_fanout
                or self._active_fanout_child_for_instance(instance_id)
            )
            next_state = (
                "busy"
                if active_task is not None or active_fanout is not None
                else "idle"
            )
            if active_task is not None:
                reason = f"recycle complete; resumed task {active_task.id}"
            elif active_fanout is not None:
                reason = (
                    "recycle complete; resumed fanout "
                    f"{active_fanout.get('fanout_id')}:{active_fanout.get('child_id')}"
                )
            else:
                reason = f"recycle complete (new session {new_session})"
            self._set_worker_state(
                instance_id, next_state,
                reason=reason,
            )
        except Exception as e:
            try:
                self.event_writer.append(ZfEvent(
                    type="worker.recycle.failed",
                    actor=instance_id,
                    payload={"role": role.name, "error": str(e)},
                ))
            except Exception:
                pass
            # Stay in "recycling" state so _check_pending doesn't retry.


    def _inject_fanout_recovery_briefing(
        self,
        role: "RoleConfig",
        fanout_child: dict,
        *,
        recovery_reason: str = "active_fanout_after_recycle",
    ) -> bool:
        """Resume a fanout child after context recycle.

        Fanout children are not kanban tasks, so normal task recovery renders
        an idle packet and loses the child. The existing fanout briefing is
        the canonical contract; re-deliver it into the fresh worker context.
        """
        path = self._fanout_child_briefing_path(role, fanout_child)
        if path is None:
            try:
                self.event_writer.append(ZfEvent(
                    type="worker.recovery.skipped",
                    actor=role.instance_id,
                    payload={
                        "role": role.name,
                        "reason": "active_fanout_briefing_missing",
                        "fanout_id": str(fanout_child.get("fanout_id") or ""),
                        "child_id": str(fanout_child.get("child_id") or ""),
                        "run_id": str(fanout_child.get("run_id") or ""),
                    },
                ))
            except Exception:
                pass
            return False
        try:
            snapshot_ref = str(fanout_child.get("snapshot_ref") or "")
            prompt = build_task_prompt(role.instance_id, path)
            context = self._dispatch_context(
                role=role,
                briefing_path=path,
                trace_id=str(fanout_child.get("trace_id") or "") or None,
            )
            self._send_transport_task(role.instance_id, path, prompt, context)
            self.event_writer.append(ZfEvent(
                type="worker.recovery.injected",
                actor=role.instance_id,
                payload={
                    "role": role.name,
                    "reason": recovery_reason,
                    "fanout_id": str(fanout_child.get("fanout_id") or ""),
                    "stage_id": str(fanout_child.get("stage_id") or ""),
                    "child_id": str(fanout_child.get("child_id") or ""),
                    "run_id": str(fanout_child.get("run_id") or ""),
                    "briefing_path": str(path),
                    "snapshot_ref": snapshot_ref,
                },
                correlation_id=str(fanout_child.get("trace_id") or "") or None,
            ))
            if not snapshot_ref:
                self.event_writer.append(ZfEvent(
                    type="runtime.snapshot.invalid",
                    actor=role.instance_id,
                    payload={
                        "source": "fanout_child",
                        "reason": "active_fanout_snapshot_missing",
                        "fanout_id": str(fanout_child.get("fanout_id") or ""),
                        "child_id": str(fanout_child.get("child_id") or ""),
                        "run_id": str(fanout_child.get("run_id") or ""),
                    },
                    correlation_id=str(fanout_child.get("trace_id") or "") or None,
                ))
        except Exception:
            return False
        return True


    def _inject_recovery_briefing(
        self,
        role: "RoleConfig",
        *,
        inject_idle_prompt: bool = True,
    ) -> bool:
        """G-RESUME-5: after a respawn, hand the agent a compact
        recovery briefing containing memory + current task + progress +
        git state + causation chain so it can resume work without
        losing continuity."""
        # Figure out the current task (if any) for this instance
        task = self._active_task_for_instance(role.instance_id)
        idle_recovery = task is None
        if idle_recovery:
            # Idle instances should come back as clean workers, not as a
            # resumed copy of their previous task. Refresh role instructions
            # without a Current Task block. Context recycle skips prompt
            # injection; watchdog respawn still injects a compact recovery note
            # so recovery delivery failures remain visible.
            try:
                idle_task = Task(
                    id="(idle)",
                    title="(idle)",
                    status="in_progress",
                    assigned_to=role.instance_id,
                )
                briefing_md = build_recovery_briefing(
                    self.state_dir,
                    role.instance_id,
                    idle_task,
                    git_context=None,
                    compact=True,
                    config=self.config,
                    project_root=self.project_root,
                )
                briefing_dir = self.state_dir / "briefings"
                briefing_dir.mkdir(parents=True, exist_ok=True)
                (briefing_dir / f"{role.instance_id}-recovery.md").write_text(
                    briefing_md,
                    encoding="utf-8",
                )
                # ZF-CONTEXT-REC-001 integration (2026-05-18): write
                # State Packet–driven recovery briefing alongside the
                # legacy one. Best-effort — recovery success uses the
                # legacy path; SP-based version is for operator /
                # downstream tooling.
                self._write_state_packet_recovery_briefing(
                    role=role,
                    task_id=getattr(idle_task, "id", None),
                    recovery_reason="idle_after_recycle",
                )
                skill_entries = self._record_skill_provenance(
                    role=role,
                    task_id=None,
                )
                instructions_dir = self.state_dir / "instructions"
                instructions_dir.mkdir(parents=True, exist_ok=True)
                (instructions_dir / f"{role.instance_id}.md").write_text(
                    generate_role_instructions(
                        self.config,
                        role,
                        task=None,
                        skill_entries=skill_entries,
                        state_dir_ref=self.state_dir,
                        project_root=getattr(self, "project_root", None),
                    ),
                    encoding="utf-8",
                )
            except Exception:
                return False
            if not inject_idle_prompt:
                try:
                    self.event_writer.append(ZfEvent(
                        type="worker.recovery.skipped",
                        actor=role.instance_id,
                        payload={
                            "role": role.name,
                            "reason": "idle_after_recycle",
                        },
                    ))
                except Exception:
                    pass
                return True
            task = idle_task
        git_context = None
        try:
            from zf.runtime.git_capture import capture_git_diff_context

            base_sha = ""
            base_sha = getattr(self, "_dispatch_heads", {}).get(task.id, "")
            git_root = self.project_root
            try:
                from zf.runtime.workdirs import WorkdirManager

                workdirs = getattr(
                    getattr(self.config, "runtime", None),
                    "workdirs",
                    None,
                )
                if workdirs is not None and getattr(workdirs, "enabled", False):
                    plan = WorkdirManager(
                        state_dir=self.state_dir,
                        project_root=self.project_root,
                        config=self.config,
                    ).plan(role)
                    project_path = Path(plan.project_path)
                    if (
                        plan.enabled
                        and plan.mode == "worktree"
                        and project_path.exists()
                    ):
                        git_root = project_path
            except Exception:
                git_root = self.project_root
            git_context = capture_git_diff_context(
                git_root,
                base_sha=base_sha,
            )
        except Exception:
            git_context = None
        try:
            if not self._ensure_recovery_contract_sufficient(role, task):
                return False
            briefing_md = build_recovery_briefing(
                self.state_dir,
                role.instance_id,
                task,
                git_context=git_context,
                compact=True,
                config=self.config,
                project_root=self.project_root,
            )
        except Exception:
            return False
        briefing_dir = self.state_dir / "briefings"
        briefing_dir.mkdir(parents=True, exist_ok=True)
        path = briefing_dir / f"{role.instance_id}-recovery.md"
        try:
            path.write_text(briefing_md, encoding="utf-8")
        except Exception:
            return False
        # Deliver via transport. Use build_task_prompt to point the
        # agent at the briefing file rather than dumping inline.
        try:
            prompt = build_task_prompt(role.instance_id, path)
            task_id = task.id if task.id != "(idle)" else None
            self._record_skill_provenance(role=role, task_id=task_id)
            context = self._dispatch_context(
                role=role,
                briefing_path=path,
                task_id=task_id,
            )
            self._send_transport_task(role.instance_id, path, prompt, context)
            self._get_spawn_coordinator().notify_first_dispatch(role)
        except Exception:
            return False
        return True


    def _ensure_recovery_contract_sufficient(
        self,
        role: "RoleConfig",
        task: Task,
    ) -> bool:
        """Fail closed when the recovery packet cannot identify enough truth.

        Context recovery is allowed to shrink or replace chat history, but it
        must not let an agent continue from an ambiguous task/stage/artifact
        boundary. This gate is read-only except for diagnostic events.
        """
        if task.id == "(idle)":
            return True
        result = run_recovery_sufficiency_gate(
            state_dir=self.state_dir,
            project_root=self.project_root,
            task=task,
            role=role,
            config=self.config,
            event_writer=self.event_writer,
        )
        if result.sufficient:
            return True
        try:
            self._set_worker_state(
                role.instance_id,
                "blocked_human",
                reason=f"recovery contract insufficient: {result.reason}",
            )
        except Exception:
            pass
        return False


    def _write_state_packet_recovery_briefing(
        self,
        *,
        role: "RoleConfig",
        task_id: str | None,
        recovery_reason: str,
    ) -> None:
        """ZF-CONTEXT-REC-001 (2026-05-18): write a State Packet-
        driven recovery briefing alongside the legacy one. Defensive
        — never raises, never blocks recovery."""
        try:
            from zf.runtime.recovery_briefing import render_recovery_briefing
            from zf.runtime.state_packet_projector import StatePacketProjector
            from zf.runtime.working_memory_projection import projection_dir

            projector = StatePacketProjector(
                state_dir=self.state_dir,
                task_store=getattr(self, "task_store", None),
                feature_store=getattr(self, "feature_store", None),
                event_log=getattr(self, "event_log", None),
            )
            project_task = task_id if task_id and task_id != "(idle)" else None
            packet = projector.project(task_id=project_task)
            try:
                state_base = self.state_dir.resolve(strict=False).relative_to(
                    self.project_root.resolve(strict=False)
                )
                state_packet_ref = str(state_base / "state" / "state-packet.json")
            except Exception:
                state_packet_ref = str(self.state_dir / "state" / "state-packet.json")
            projection_refs: list[str] = []
            if packet.task_id:
                pdir = projection_dir(self.state_dir, packet.task_id)
                for name in (
                    "plan.md", "findings.md",
                    "progress.md", "attempt-ledger.md",
                ):
                    fpath = pdir / name
                    if fpath.exists():
                        projection_refs.append(str(fpath))
            briefing = render_recovery_briefing(
                packet,
                state_packet_ref=state_packet_ref,
                projection_refs=projection_refs,
                recovery_reason=recovery_reason,
            )
            target_dir = self.state_dir / "briefings"
            target_dir.mkdir(parents=True, exist_ok=True)
            (
                target_dir / f"{role.instance_id}-recovery-state-packet.md"
            ).write_text(briefing, encoding="utf-8")
        except Exception:
            pass

    def _report_stuck_worker(self, role: "RoleConfig") -> "OrchestratorDecision":
        """Emit worker.stuck and try to recover the active task/worker."""
        reason = (
            f"worker {role.instance_id} produced no new output for "
            f"{role.stuck_threshold_seconds:.0f}s"
        )
        task = self._active_task_for_instance(role.instance_id)
        dispatch = self._latest_dispatch_event_for_task(task.id) if task else None
        dispatch_payload = dispatch.payload if dispatch is not None else {}
        dispatch_id = ""
        briefing = ""
        task_dispatch_id = getattr(task, "active_dispatch_id", "") if task else ""
        if isinstance(dispatch_payload, dict):
            dispatch_id = str(
                dispatch_payload.get("dispatch_id")
                or task_dispatch_id
                or ""
            )
            briefing = str(dispatch_payload.get("briefing") or "")
        elif task is not None:
            dispatch_id = task_dispatch_id
        pane_command = self._pane_current_command(role.instance_id)
        session_id = ""
        try:
            reg = RoleSessionRegistry(
                self.state_dir / "role_sessions.yaml",
                project_root=str(self.project_root),
            )
            cached = reg.get(role.instance_id)
            session_id = str(cached) if cached else ""
        except Exception:
            session_id = ""
        stuck_event = ZfEvent(
            type="worker.stuck",
            actor=role.instance_id,
            task_id=task.id if task is not None else None,
            payload={
                "role": role.name,
                "instance_id": role.instance_id,
                "threshold_seconds": role.stuck_threshold_seconds,
                "task_id": task.id if task is not None else "",
                "dispatch_id": dispatch_id,
                "briefing": briefing,
                "pane_current_command": pane_command,
                "role_session_id": session_id,
            },
        )
        try:
            self.event_writer.append(stuck_event)
        except Exception:
            pass
        self._set_worker_state(
            role.instance_id, "stuck",
            reason=f"no output for {role.stuck_threshold_seconds:.0f}s",
        )

        progress_event = (
            self._latest_unrejected_progress_event_for_dispatch(
                task.id,
                dispatch_id,
            )
            if task is not None else None
        )
        if progress_event is not None:
            self._recover_stuck_worker_with_recorded_progress(
                role=role,
                task=task,
                stuck_event=stuck_event,
                progress_event=progress_event,
                dispatch_id=dispatch_id,
            )
            return OrchestratorDecision(
                action="recover",
                task_id=task.id,
                role=role.instance_id,
                reason=(
                    "worker.stuck ignored: "
                    f"{progress_event.type} already recorded"
                ),
            )

        manifest_recovery = self._request_manifest_terminal_completion_if_pending(
            role=role,
            task=task,
            reason="worker_stuck",
            inject_prompt=True,
            causation_id=stuck_event.id,
        )
        if manifest_recovery is not None:
            return manifest_recovery

        # DID-9 (2026-06-19 e2e): a writer worker that did the implementation but
        # ended its turn WITHOUT emitting its terminal event (no manifest, no
        # progress event — e.g. dev-core implemented the fix, left it uncommitted,
        # and never ran `zf emit workflow.child.completed`) must be NUDGED to run
        # its completion protocol, not requeued — requeuing discards the
        # uncommitted work and respawns from scratch. Nudge once; on the next
        # stuck cycle fall through to the requeue below.
        completion_nudge = self._request_terminal_completion_nudge(
            role=role,
            task=task,
            dispatch_id=dispatch_id,
            reason="worker_stuck_no_progress",
            causation_id=stuck_event.id,
        )
        if completion_nudge is not None:
            return completion_nudge

        requeued = task is None
        from_status = getattr(task, "status", "") if task is not None else ""
        from_assignee = getattr(task, "assigned_to", "") or ""
        recovery_assignee = role.instance_id or role.name
        if task is not None:
            try:
                updated = self.task_store.update(
                    task.id,
                    status="backlog",
                    assigned_to=recovery_assignee,
                    active_dispatch_id="",
                )
                requeued = updated is not None
                if requeued:
                    self._active_dispatch_ids.pop(task.id, None)
                    self._dispatch_epoch.pop(task.id, None)
                    self.event_writer.append(ZfEvent(
                        type="task.requeued",
                        actor="zf-cli",
                        task_id=task.id,
                        causation_id=stuck_event.id,
                        payload={
                            "source": "worker_stuck_recovery",
                            "from_status": from_status,
                            "from_assignee": from_assignee,
                            "dispatch_id": dispatch_id,
                            "role": role.name,
                            "instance_id": role.instance_id,
                            "recovery_assignee": recovery_assignee,
                        },
                    ))
                    if recovery_assignee:
                        self.event_writer.append(ZfEvent(
                            type="task.assigned",
                            actor="zf-cli",
                            task_id=task.id,
                            causation_id=stuck_event.id,
                            payload={
                                "role": role.name,
                                "assignee": recovery_assignee,
                                "source": "worker_stuck_recovery",
                                "trigger_event": "worker.stuck",
                            },
                        ))
            except Exception:
                requeued = False

        recovery = None
        if requeued:
            try:
                recovery = self._respawn_instance(role)
            except Exception as e:
                recovery = OrchestratorDecision(
                    action="respawn_failed",
                    role=role.instance_id,
                    reason=f"watchdog respawn failed: {e}",
                )
            if recovery.action == "respawn":
                detector = getattr(self, "_stuck_detectors", {}).get(
                    role.instance_id
                )
                if detector is not None:
                    try:
                        detector.reset()
                    except Exception:
                        pass
                # Do not clear _stuck_already_reported here: respawn/reset is
                # not proof that the pane produced new output. The capture loop
                # clears the dedupe only after it observes a new fingerprint.
                self._set_worker_state(
                    role.instance_id,
                    "idle",
                    reason="worker.stuck recovered",
                )
                try:
                    self.event_writer.append(ZfEvent(
                        type="worker.stuck.recovered",
                        actor=role.instance_id,
                        task_id=task.id if task is not None else None,
                        causation_id=stuck_event.id,
                        payload={
                            "role": role.name,
                            "instance_id": role.instance_id,
                            "task_id": task.id if task is not None else "",
                            "dispatch_id": dispatch_id,
                            "recovery_action": "respawn",
                        },
                    ))
                except Exception:
                    pass
                return OrchestratorDecision(
                    action="recover",
                    task_id=task.id if task is not None else None,
                    role=role.instance_id,
                    reason=f"worker.stuck recovered: {role.instance_id}",
                )

        failure_reason = "task requeue failed"
        if requeued and recovery is not None:
            failure_reason = recovery.reason or recovery.action
        try:
            self.event_writer.append(ZfEvent(
                type="worker.stuck.recovery_failed",
                actor=role.instance_id,
                task_id=task.id if task is not None else None,
                causation_id=stuck_event.id,
                payload={
                    "role": role.name,
                    "instance_id": role.instance_id,
                    "task_id": task.id if task is not None else "",
                    "dispatch_id": dispatch_id,
                    "reason": failure_reason,
                },
            ))
        except Exception:
            pass
        # doc 79 Tier1 (R13 fix, backlog 2026-06-06-0401 §D): recovery_failed IS
        # the "infra retry exhausted for this incident" signal. R13 showed
        # respawn failures spread thin across lanes so the per-window cap path
        # (_record_respawn_failure cooldown) never fired — hook the real
        # recovery-gave-up event so the cascade routes escalate / safe-halt
        # regardless of the cap counter. attempts=cap → decide_cascade skips the
        # retry branch (recovery already gave up).
        self._emit_respawn_cascade(
            role.instance_id, attempts=self._RESPAWN_FAILURE_MAX_CONSECUTIVE,
        )
        # 2026-06-10 review: park in blocked_human (not "stuck") so the
        # dead-pane watchdog guard skips this worker — recovery already
        # gave up; "stuck" let the watchdog keep re-respawning a
        # structurally-broken worker every tick. zf start clears
        # blocked_human as the operator intervention.
        self._set_worker_state(
            role.instance_id,
            "blocked_human",
            reason=f"worker.stuck recovery failed: {failure_reason}",
        )
        try:
            self.escalation.escalate(reason)
        except Exception:
            pass
        return OrchestratorDecision(
            action="escalate",
            role=role.instance_id,
            reason=f"worker.stuck: {role.instance_id}",
        )

    # -- helpers --

    def _wait_role_ready(self, role: "RoleConfig") -> bool:
        try:
            from zf.runtime.backend import get_adapter
            adapter = get_adapter(role.backend)
            if not adapter.requires_ready_wait:
                return True
            # 2026-05-15 (r5 discovery): claude cold-boot can take 60-90s
            # under cold-cache + multi-pane contention. A hardcoded 30s
            # timeout here causes _respawn_instance to raise, the watchdog
            # re-fires every ~30s, and the worker can never finish booting.
            # Use role.spawn_ready_timeout_seconds (override) or a generous
            # default (120s) so first boot succeeds even on slow systems.
            timeout = float(
                getattr(role, "spawn_ready_timeout_seconds", 0)
                or 120.0
            )
            ready = self.transport.wait_ready(
                role.instance_id,
                adapter.ready_pattern,
                timeout=timeout,
            )
            if ready and adapter.post_ready_delay_s > 0:
                time.sleep(adapter.post_ready_delay_s)
            return bool(ready)
        except Exception:
            return False

    def _recover_stuck_worker_with_recorded_progress(
        self,
        *,
        role: "RoleConfig",
        task: Task,
        stuck_event: ZfEvent,
        progress_event: ZfEvent,
        dispatch_id: str,
    ) -> None:
        detector = getattr(self, "_stuck_detectors", {}).get(role.instance_id)
        if detector is not None:
            try:
                detector.reset()
            except Exception:
                pass
        self._stuck_already_reported.discard(role.instance_id)
        worker_state = self._worker_state_after_progress_event(progress_event)
        self._set_worker_state(
            role.instance_id,
            worker_state,
            reason=(
                f"{progress_event.type} already recorded for task {task.id}; "
                "skipping stuck requeue"
            ),
            task_id=task.id,
        )
        try:
            self.event_writer.append(ZfEvent(
                type="worker.stuck.recovered",
                actor=role.instance_id,
                task_id=task.id,
                causation_id=stuck_event.id,
                correlation_id=progress_event.correlation_id,
                payload={
                    "role": role.name,
                    "instance_id": role.instance_id,
                    "task_id": task.id,
                    "dispatch_id": dispatch_id,
                    "recovery_action": "progress_already_recorded",
                    "progress_event": progress_event.type,
                    "progress_event_id": progress_event.id,
                },
            ))
        except Exception:
            pass

    def _request_manifest_terminal_completion_if_pending(
        self,
        *,
        role: "RoleConfig",
        task: Task | None,
        reason: str,
        inject_prompt: bool,
        causation_id: str | None = None,
    ) -> OrchestratorDecision | None:
        """Recover an artifact-first role that published durable refs but
        has not emitted its terminal event yet.

        This is deliberately narrower than normal stuck recovery: the task is
        not requeued and the worker is not asked to regenerate artifacts. The
        only requested action is ownership guard + terminal event emission.
        """
        if task is None:
            return None
        dispatch_id = getattr(task, "active_dispatch_id", "") or ""
        if not dispatch_id:
            dispatch = self._latest_dispatch_event_for_task(task.id)
            payload = dispatch.payload if dispatch is not None else {}
            if isinstance(payload, dict):
                dispatch_id = str(payload.get("dispatch_id") or "")
        expected_event = self._expected_terminal_event_for_role(role)
        if not expected_event:
            return None
        manifest_event = self._latest_manifest_pending_terminal_event(
            task_id=task.id,
            dispatch_id=dispatch_id,
            role=role,
            expected_event=expected_event,
        )
        if manifest_event is None:
            return None

        registry = getattr(self, "_manifest_completion_requests", None)
        if registry is None:
            registry = set()
            self._manifest_completion_requests = registry
        key = (role.instance_id, task.id, dispatch_id, manifest_event.id)
        first_request = key not in registry
        registry.add(key)

        detector = getattr(self, "_stuck_detectors", {}).get(role.instance_id)
        if detector is not None:
            try:
                detector.reset()
            except Exception:
                pass
        self._stuck_already_reported.discard(role.instance_id)
        self._set_worker_state(
            role.instance_id,
            "completion_pending",
            reason=(
                f"{manifest_event.type} already recorded for task {task.id}; "
                f"waiting for {expected_event}"
            ),
        )

        prompt_path = ""
        prompt_error = ""
        prompt_injected = False
        if first_request and inject_prompt:
            try:
                prompt_path = str(self._inject_manifest_terminal_completion_prompt(
                    role=role,
                    task=task,
                    dispatch_id=dispatch_id,
                    manifest_event=manifest_event,
                    expected_event=expected_event,
                ))
                prompt_injected = True
            except Exception as exc:
                prompt_error = str(exc)

        if first_request:
            try:
                self.event_writer.append(ZfEvent(
                    type="worker.stuck.recovered",
                    actor=role.instance_id,
                    task_id=task.id,
                    causation_id=causation_id or manifest_event.id,
                    correlation_id=manifest_event.correlation_id,
                    payload={
                        "role": role.name,
                        "instance_id": role.instance_id,
                        "task_id": task.id,
                        "dispatch_id": dispatch_id,
                        "recovery_action": "terminal_completion_requested",
                        "reason": reason,
                        "progress_event": manifest_event.type,
                        "progress_event_id": manifest_event.id,
                        "expected_event": expected_event,
                        "prompt_path": prompt_path,
                        "prompt_injected": prompt_injected,
                        "prompt_error": prompt_error,
                    },
                ))
            except Exception:
                pass
        return OrchestratorDecision(
            action="recover",
            task_id=task.id,
            role=role.instance_id,
            reason=(
                f"{manifest_event.type} already recorded; "
                f"requested {expected_event}"
            ),
        )

    def _inject_manifest_terminal_completion_prompt(
        self,
        *,
        role: "RoleConfig",
        task: Task,
        dispatch_id: str,
        manifest_event: ZfEvent,
        expected_event: str,
    ) -> Path:
        briefing_dir = self.state_dir / "briefings"
        briefing_dir.mkdir(parents=True, exist_ok=True)
        path = (
            briefing_dir
            / f"{role.instance_id}-{task.id}-terminal-completion.md"
        )
        lines = [
            f"Active task: {task.id}",
            "",
            "# Terminal Completion Recovery",
            "",
            (
                "A durable `artifact.manifest.published` event is already "
                "recorded for this dispatch. Do not rewrite, regenerate, or "
                "replace the published artifacts unless the guard below proves "
                "your ownership is stale."
            ),
            "",
            "## Required action",
            (
                f"1. Run `{zf_cli_cmd()} guard ownership --task {task.id} "
                f"--actor {role.instance_id}`."
            ),
            (
                "2. If the guard passes, emit the missing terminal event "
                f"`{expected_event}` with the active dispatch id "
                f"`{dispatch_id}`."
            ),
            (
                "3. Reference the published manifest event id and artifact refs "
                "in the terminal payload; keep `changed_files` empty for "
                "read-only design/gate roles."
            ),
            "",
            "## Existing manifest",
            f"- event_id: `{manifest_event.id}`",
            f"- dispatch_id: `{dispatch_id or '-'}`",
        ]
        for ref in self._manifest_artifact_refs_for_prompt(manifest_event):
            lines.append(f"- artifact: {ref}")
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        prompt = build_task_prompt(role.instance_id, path)
        context = self._dispatch_context(
            role=role,
            briefing_path=path,
            task_id=task.id,
        )
        self._send_transport_task(role.instance_id, path, prompt, context)
        return path

    def _request_terminal_completion_nudge(
        self,
        *,
        role: "RoleConfig",
        task: Task | None,
        dispatch_id: str,
        reason: str,
        causation_id: str | None = None,
    ) -> "OrchestratorDecision | None":
        """DID-9: nudge a worker that did its task but never emitted its terminal
        event (no manifest, no progress event) to run its completion protocol,
        instead of requeuing (which loses its uncommitted work). Nudges once per
        (instance, task, dispatch); the next stuck cycle falls through to requeue.
        """
        if task is None:
            return None
        expected_event = self._expected_terminal_event_for_role(role)
        if not expected_event:
            return None
        registry = getattr(self, "_completion_nudge_requests", None)
        if registry is None:
            registry = set()
            self._completion_nudge_requests = registry
        key = (role.instance_id, task.id, dispatch_id)
        if key in registry:
            return None  # already nudged once → let the caller requeue
        registry.add(key)

        detector = getattr(self, "_stuck_detectors", {}).get(role.instance_id)
        if detector is not None:
            try:
                detector.reset()
            except Exception:
                pass
        self._stuck_already_reported.discard(role.instance_id)
        self._set_worker_state(
            role.instance_id,
            "completion_pending",
            reason=f"did work but never emitted {expected_event}; nudging to complete",
        )
        prompt_injected = False
        prompt_error = ""
        try:
            self._inject_terminal_completion_nudge_prompt(
                role=role,
                task=task,
                dispatch_id=dispatch_id,
                expected_event=expected_event,
            )
            prompt_injected = True
        except Exception as exc:  # noqa: BLE001 — best-effort nudge
            prompt_error = str(exc)
        try:
            self.event_writer.append(ZfEvent(
                type="worker.stuck.recovered",
                actor=role.instance_id,
                task_id=task.id,
                causation_id=causation_id or "",
                payload={
                    "role": role.name,
                    "instance_id": role.instance_id,
                    "task_id": task.id,
                    "dispatch_id": dispatch_id,
                    "recovery_action": "completion_nudge_requested",
                    "reason": reason,
                    "expected_event": expected_event,
                    "prompt_injected": prompt_injected,
                    "prompt_error": prompt_error,
                },
            ))
        except Exception:
            pass
        return OrchestratorDecision(
            action="recover",
            task_id=task.id,
            role=role.instance_id,
            reason=(
                f"completion nudge: re-sent {expected_event} instruction "
                "instead of requeue"
            ),
        )

    def _inject_terminal_completion_nudge_prompt(
        self,
        *,
        role: "RoleConfig",
        task: Task,
        dispatch_id: str,
        expected_event: str,
    ) -> Path:
        briefing_dir = self.state_dir / "briefings"
        briefing_dir.mkdir(parents=True, exist_ok=True)
        path = briefing_dir / f"{role.instance_id}-{task.id}-completion-nudge.md"
        lines = [
            f"Active task: {task.id}",
            "",
            "# Completion required — you have not emitted your terminal event",
            "",
            (
                "You appear to have done the work for this task but ended your turn "
                "WITHOUT emitting the terminal completion event. The harness cannot "
                "proceed until you emit it. Do NOT redo, rewrite, or re-plan the "
                "implementation — your existing changes are intact."
            ),
            "",
            "## Required action (run these now, in order)",
            (
                f"1. `{zf_cli_cmd()} guard ownership --task {task.id} "
                f"--actor {role.instance_id}` — if it exits non-zero, STOP "
                "(your ownership is stale)."
            ),
            (
                "2. If you have uncommitted changes in this worktree, COMMIT them "
                "before completing. Stage only this task's changed files with explicit "
                "pathspecs (`git add -- <path>...`); never use `git add -A`, `git add .`, "
                "or `git commit -a`. An uncommitted task file is rejected at integration."
            ),
            (
                f"3. Emit your terminal event `{expected_event}` with the active "
                f"dispatch id `{dispatch_id or '-'}` (see your role instructions "
                "for the exact command). If the work does NOT meet acceptance, "
                "emit the failure event instead."
            ),
        ]
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        prompt = build_task_prompt(role.instance_id, path)
        context = self._dispatch_context(
            role=role,
            briefing_path=path,
            task_id=task.id,
        )
        self._send_transport_task(role.instance_id, path, prompt, context)
        return path

    def _rejected_artifact_manifest_event_ids(
        self,
        events: list[ZfEvent],
        task_id: str,
    ) -> set[str]:
        rejected: set[str] = set()
        for event in events:
            if event.task_id != task_id or event.type != "artifact.manifest.rejected":
                continue
            payload = event.payload if isinstance(event.payload, dict) else {}
            origin_id = str(
                payload.get("trigger_event_id")
                or payload.get("origin_event_id")
                or "",
            )
            if origin_id:
                rejected.add(origin_id)
        return rejected
