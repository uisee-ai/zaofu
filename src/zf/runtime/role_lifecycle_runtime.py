"""Role-scoped provider process activation and hibernation.

Logical role identity, affinity, workdir, and provider session metadata remain
durable. Only the physical provider process is created on first dispatch and
removed after a mechanically safe idle period.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from zf.core.config.schema import RoleConfig
from zf.core.events.model import ZfEvent
from zf.core.state.locks import FileLock
from zf.core.state.role_sessions import RoleSessionRegistry
from zf.runtime.event_window import read_runtime_events
from zf.runtime.git_capture import capture_git_state
from zf.runtime.run_admission import fold_terminal_run_scope
from zf.runtime.transport import (
    transport_error_diagnostics,
    transport_readiness_error,
)
from zf.runtime.workflow_operation import (
    TERMINAL_OPERATION_STATUSES,
    reduce_workflow_operations,
)


_INACTIVE_LIFECYCLE_STATES = frozenset({"dormant", "suspended"})
_TRANSITIONAL_LIFECYCLE_STATES = frozenset({
    "activating",
    "resuming",
    "suspending",
})
_NORMAL_SUSPEND_DEFERRALS = frozenset({
    "assigned_task_present",
    "cooldown",
    "idle_threshold_not_reached",
    "lifecycle_transition_in_progress",
    "provider_operation_active",
    "runnable_task_present",
})


class RoleLifecycleRuntimeMixin:
    """Host mixin for on-demand role process lifecycle."""

    def _clear_role_skill_treatment(self, instance_id: str) -> None:
        """Forget session-local treatment only after its work is quiescent."""
        getattr(self, "_active_skill_treatment_tasks", {}).pop(instance_id, None)
        cache = getattr(self, "_activation_skill_provenance", {})
        for key in [key for key in cache if key[0] == instance_id]:
            cache.pop(key, None)

    def _record_skill_provenance(
        self,
        *,
        role: RoleConfig,
        task_id: str | None = None,
    ) -> list:
        from zf.runtime.skill_dispatch_treatment import (
            SkillDispatchTreatmentError,
        )

        if self._role_is_on_demand(role):
            active_tasks = getattr(self, "_active_skill_treatment_tasks", {})
            active_task = str(active_tasks.get(role.instance_id) or "")
            if not task_id and active_task:
                raise SkillDispatchTreatmentError(
                    "task_id is required while the on-demand role has a "
                    f"task-scoped Skill treatment for {active_task}"
                )
            self._ensure_role_active(role, task_id=task_id)
            cache = getattr(self, "_activation_skill_provenance", {})
            cached = cache.pop(
                (role.instance_id, str(task_id or "")),
                None,
            )
            if cached is not None:
                return cached
        try:
            return self._materialize_role_skills_raw(
                role=role,
                task_id=task_id,
            )
        except SkillDispatchTreatmentError:
            raise
        except Exception:
            return []

    def _materialize_role_skills_raw(
        self,
        *,
        role: RoleConfig,
        task_id: str | None = None,
        execution_project_root: Path | None = None,
        execution_runtime_root: Path | None = None,
    ) -> list:
        """Materialize skills after the role workdir exists."""
        from zf.core.skills import (
            build_skill_lock_entries,
            materialize_role_skills,
            upsert_skills_lockfile,
        )
        from zf.runtime.evolution_skill_overlay import resolve_skill_overlays
        from zf.runtime.skill_dispatch_treatment import (
            freeze_skill_dispatch_treatment,
        )

        task_family = ""
        if task_id:
            from zf.core.task.store import TaskStore

            task = TaskStore(self.state_dir / "kanban.json").get(task_id)
            if task is not None:
                task_family = str(
                    task.contract.campaign or task.contract.phase or ""
                )
        overlays = resolve_skill_overlays(
            self.state_dir,
            role_instance=role.instance_id,
            task_family=task_family,
            cohort=task_id or "",
            project_root=self.project_root,
        ) if task_id else None
        overlay_paths = overlays.paths if overlays is not None else {}
        if not role.skills and not overlay_paths:
            return []

        materialized = materialize_role_skills(
            config=self.config,
            project_root=self.project_root,
            state_dir=self.state_dir,
            role=role,
            task_id=task_id,
            execution_project_root=execution_project_root,
            execution_runtime_root=execution_runtime_root,
            skill_overrides=overlay_paths,
        )
        materialized_paths = (
            materialized.materialized_paths_under(self.project_root)
            if materialized is not None else {}
        )
        entries = build_skill_lock_entries(
            project_root=self.project_root,
            state_dir=self.state_dir,
            role=role,
            config=self.config,
            task_id=task_id,
            run_id=self._current_run_id(),
            materialized_paths=materialized_paths,
            skill_overrides=overlay_paths,
        )
        upsert_skills_lockfile(state_dir=self.state_dir, entries=entries)
        if materialized is not None:
            manifest_payload = materialized.to_payload()
            treatment = freeze_skill_dispatch_treatment(
                state_dir=self.state_dir,
                role_instance=role.instance_id,
                task_id=str(task_id or ""),
                run_id=str(self._current_run_id() or ""),
                selected_overlays=overlays.selected if overlays else (),
                lock_entries=entries,
                manifest_payload=manifest_payload,
            )
            if task_id:
                active_tasks = getattr(
                    self,
                    "_active_skill_treatment_tasks",
                    None,
                )
                if active_tasks is None:
                    active_tasks = {}
                    self._active_skill_treatment_tasks = active_tasks
                active_tasks[role.instance_id] = str(task_id)
            self.event_writer.append(ZfEvent(
                type="skills.materialized",
                actor="zf-cli",
                task_id=task_id,
                payload={
                    **manifest_payload,
                    "skill_dispatch_treatment_ref": str(
                        treatment.get("ref") or ""
                    ),
                    "skill_dispatch_treatment_digest": str(
                        treatment.get("sha256") or ""
                    ),
                    "overlay": {
                        "selected": list(overlays.selected) if overlays else [],
                        "excluded_count": len(overlays.excluded) if overlays else 0,
                    },
                },
            ))
        return entries

    def _role_is_on_demand(self, role: RoleConfig) -> bool:
        return role.lifecycle.mode == "on_demand"

    def _activate_role_for_task_dispatch(self, task: Any, role: RoleConfig) -> bool:
        """Activate an on-demand role before dispatch mutates task state."""
        try:
            self._ensure_role_active(role, task_id=task.id)
            return True
        except Exception as exc:
            try:
                diagnostics = transport_error_diagnostics(exc)
                self.event_writer.append(ZfEvent(
                    type="orchestrator.dispatch_failed",
                    actor="zf-cli",
                    task_id=task.id,
                    payload={
                        "role": role.name,
                        "assignee": role.instance_id,
                        "stage": "role_activation",
                        "error": str(exc)[:500],
                        **diagnostics,
                    },
                ))
                self._emit_dispatch_skipped(
                    task=task,
                    role=role,
                    reason="role_activation_failed",
                )
            except Exception:
                pass
            self._record_dispatch_failure(task.id)
            return False

    def _role_lifecycle_registry(self) -> RoleSessionRegistry:
        return RoleSessionRegistry(
            self.state_dir / "role_sessions.yaml",
            project_root=str(self.project_root),
        )

    def _role_lifecycle_meta(self, role: RoleConfig) -> dict[str, Any]:
        return self._role_lifecycle_registry().instance_meta().get(
            role.instance_id,
            {},
        )

    def _ensure_role_active(
        self,
        role: RoleConfig,
        *,
        task_id: str | None = None,
        spawn_cwd: Path | None = None,
        skill_runtime_root: Path | None = None,
    ) -> bool:
        """Start one on-demand role before any dispatch state is mutated.

        Returns True only when this call allocated a new physical process.
        Eager/resident roles and already-live on-demand roles are no-ops.
        """
        if not self._role_is_on_demand(role):
            return False
        initial_meta = self._role_lifecycle_meta(role)
        try:
            if (
                self.transport.is_alive(role.instance_id)
                and str(initial_meta.get("lifecycle_state") or "") == "active"
            ):
                self._mark_role_lifecycle_active(role)
                return False
        except Exception:
            pass

        lock_name = re.sub(r"[^A-Za-z0-9_.-]+", "-", role.instance_id)
        lock_path = (
            self.state_dir
            / "locks"
            / "role-lifecycle"
            / f"{lock_name}.activation"
        )
        with FileLock(lock_path, timeout_seconds=180.0):
            locked_meta = self._role_lifecycle_meta(role)
            try:
                if self.transport.is_alive(role.instance_id):
                    if not self._wait_role_ready(role):
                        raise transport_readiness_error(
                            self.transport,
                            role.instance_id,
                            backend=role.backend,
                        )
                    self._mark_role_lifecycle_active(role)
                    self._set_worker_state(
                        role.instance_id,
                        "idle",
                        reason="on-demand provider readiness recovered",
                        force=True,
                    )
                    return False
            except Exception:
                if str(locked_meta.get("lifecycle_state") or "") == "active":
                    raise

            registry = self._role_lifecycle_registry()
            meta = registry.instance_meta().get(role.instance_id, {})
            previous = str(meta.get("lifecycle_state") or "dormant")
            if previous not in _INACTIVE_LIFECYCLE_STATES:
                previous = "suspended" if meta.get("spawned_at") else "dormant"
            transition = "resuming" if previous == "suspended" else "activating"
            started = self._now()
            registry.update_instance_meta(
                role.instance_id,
                lifecycle_state=transition,
                lifecycle_previous_state=previous,
                lifecycle_transition_at=_iso_from_epoch(started),
                lifecycle_activation_task_id=str(task_id or ""),
            )
            self._set_worker_state(
                role.instance_id,
                transition,
                reason="on-demand provider activation started",
                force=True,
            )
            self._emit_role_lifecycle_event(
                "role.lifecycle.activation.started",
                role,
                task_id=task_id,
                payload={
                    "from": previous,
                    "to": transition,
                    "preserve_session": role.lifecycle.preserve_session,
                    "preserve_workdir": role.lifecycle.preserve_workdir,
                },
            )

            launch_was_resume = False
            try:
                effective_spawn_cwd = (
                    Path(spawn_cwd).resolve()
                    if spawn_cwd is not None
                    else self._role_spawn_cwd(
                        role,
                        source="on_demand_activation",
                    )
                )
                materialized = self._materialize_role_skills_raw(
                    role=role,
                    task_id=task_id,
                    execution_project_root=effective_spawn_cwd,
                    execution_runtime_root=skill_runtime_root,
                )
                if materialized:
                    cache = getattr(
                        self,
                        "_activation_skill_provenance",
                        None,
                    )
                    if cache is None:
                        cache = {}
                        self._activation_skill_provenance = cache
                    cache[(role.instance_id, str(task_id or ""))] = materialized
                self._write_on_demand_role_instructions(
                    role,
                    task_id=task_id,
                    skill_entries=materialized,
                )
                self._get_spawn_coordinator().spawn(
                    role,
                    cwd=effective_spawn_cwd,
                )
                launch_was_resume = self._latest_role_launch_is_resume(
                    role.instance_id
                )
                if not self._wait_role_ready(role):
                    raise transport_readiness_error(
                        self.transport,
                        role.instance_id,
                        backend=role.backend,
                    )
            except Exception as exc:
                try:
                    self.transport.terminate(role.instance_id)
                except Exception:
                    pass
                registry = self._role_lifecycle_registry()
                if (
                    role.backend == "codex"
                    and launch_was_resume
                ):
                    registry.clear(role.instance_id)
                    self._emit_role_lifecycle_event(
                        "role.lifecycle.continuity.downgraded",
                        role,
                        task_id=task_id,
                        payload={
                            "reason": "codex_resume_readiness_failed",
                            "previous_state": previous,
                            "fallback": "fresh_provider_session",
                        },
                    )
                registry.update_instance_meta(
                    role.instance_id,
                    lifecycle_state=previous,
                    lifecycle_transition_at=_iso_from_epoch(self._now()),
                    lifecycle_last_error=str(exc)[:500],
                )
                self._set_worker_state(
                    role.instance_id,
                    previous,
                    reason=f"on-demand provider activation failed: {exc}",
                    force=True,
                )
                self._emit_role_lifecycle_event(
                    "role.lifecycle.activation.failed",
                    role,
                    task_id=task_id,
                    payload={
                        "from": transition,
                        "to": previous,
                        "reason": str(exc)[:500],
                        **transport_error_diagnostics(exc),
                    },
                )
                raise

            now = self._now()
            registry = self._role_lifecycle_registry()
            registry.update_instance_meta(
                role.instance_id,
                lifecycle_state="active",
                lifecycle_transition_at=_iso_from_epoch(now),
                lifecycle_active_at=_iso_from_epoch(now),
                lifecycle_last_error="",
            )
            registry.record_heartbeat(role.instance_id, {
                "instance_id": role.instance_id,
                "state": "idle",
                "current_task_id": "",
                "last_action_ts": _iso_from_epoch(now),
                "source": "role.lifecycle.ready",
            })
            self._set_worker_state(
                role.instance_id,
                "idle",
                reason="on-demand provider ready",
                force=True,
            )
            is_resume = self._latest_role_launch_is_resume(role.instance_id)
            if previous == "suspended" and not is_resume:
                self._emit_role_lifecycle_event(
                    "role.lifecycle.continuity.downgraded",
                    role,
                    task_id=task_id,
                    payload={
                        "reason": "provider_session_resume_not_proven",
                        "previous_state": previous,
                    },
                )
            self._emit_role_lifecycle_event(
                "role.lifecycle.ready",
                role,
                task_id=task_id,
                payload={
                    "from": transition,
                    "to": "active",
                    "is_resume": is_resume,
                    "startup_seconds": round(max(0.0, now - started), 3),
                },
            )
            return True

    def _write_on_demand_role_instructions(
        self,
        role: RoleConfig,
        *,
        task_id: str | None,
        skill_entries: list,
    ) -> None:
        from zf.runtime.injection import generate_role_instructions

        task = None
        if task_id:
            try:
                task = self.task_store.get(task_id)
            except Exception:
                task = None
        instructions = generate_role_instructions(
            self.config,
            role,
            task=task,
            skill_entries=skill_entries,
            state_dir_ref=self.state_dir,
            project_root=self.project_root,
        )
        instructions_dir = self.state_dir / "instructions"
        instructions_dir.mkdir(parents=True, exist_ok=True)
        (instructions_dir / f"{role.instance_id}.md").write_text(
            instructions,
            encoding="utf-8",
        )

    def _mark_role_lifecycle_active(self, role: RoleConfig) -> None:
        meta = self._role_lifecycle_meta(role)
        if str(meta.get("lifecycle_state") or "") == "active":
            return
        now = self._now()
        self._role_lifecycle_registry().update_instance_meta(
            role.instance_id,
            lifecycle_state="active",
            lifecycle_transition_at=_iso_from_epoch(now),
            lifecycle_active_at=(
                str(meta.get("lifecycle_active_at") or "")
                or _iso_from_epoch(now)
            ),
        )

    def _hibernate_idle_roles(self) -> None:
        """Suspend eligible on-demand provider processes."""
        now = self._now()
        for role in self.all_roles():
            if not self._role_is_on_demand(role):
                continue
            if role.name in {"orchestrator", "run-manager"}:
                continue
            try:
                alive = self.transport.is_alive(role.instance_id)
            except Exception:
                alive = False
            if not alive:
                continue
            if self._last_worker_state.get(role.instance_id, "idle") != "idle":
                continue
            eligible, reason, details = self._role_suspend_admission(
                role,
                now=now,
            )
            if not eligible:
                if reason not in _NORMAL_SUSPEND_DEFERRALS:
                    self._record_suspend_rejection(
                        role,
                        reason=reason,
                        details=details,
                        now=now,
                    )
                continue
            self._suspend_role(role, now=now)

    def _role_suspend_admission(
        self,
        role: RoleConfig,
        *,
        now: float,
    ) -> tuple[bool, str, dict[str, Any]]:
        registry = self._role_lifecycle_registry()
        meta = registry.instance_meta().get(role.instance_id, {})
        state = str(meta.get("lifecycle_state") or "active")
        if state in _TRANSITIONAL_LIFECYCLE_STATES:
            return False, "lifecycle_transition_in_progress", {"state": state}
        last_transition = _epoch_from_value(meta.get("lifecycle_transition_at"))
        if (
            last_transition is not None
            and now - last_transition < role.lifecycle.cooldown_seconds
        ):
            return False, "cooldown", {}

        _heartbeat_at, heartbeat = registry.get_last_heartbeat(role.instance_id)
        heartbeat = heartbeat or {}
        last_action = _epoch_from_value(heartbeat.get("last_action_ts"))
        if last_action is None:
            last_action = _epoch_from_value(meta.get("lifecycle_active_at"))
        if last_action is None:
            return False, "idle_currentness_unproven", {}
        idle_seconds = max(0.0, now - last_action)
        if idle_seconds < role.lifecycle.idle_seconds:
            return False, "idle_threshold_not_reached", {
                "idle_seconds": round(idle_seconds, 3),
            }

        assigned = [
            task.id
            for task in self.task_store.list_all()
            if str(task.assigned_to or "") in {role.instance_id, role.name}
        ]
        if assigned:
            return False, "assigned_task_present", {"task_ids": assigned}
        ready = self._runnable_task_ids_for_role(role)
        if ready:
            return False, "runnable_task_present", {"task_ids": ready}

        runtime_events = read_runtime_events(self.event_log, self.state_dir)
        operations = reduce_workflow_operations(runtime_events)
        run_alias_map, terminal_runs = fold_terminal_run_scope(runtime_events)
        active_operations = [
            operation_id
            for operation_id, operation in operations.items()
            if str(operation.get("role_instance") or "") == role.instance_id
            and str(operation.get("status") or "")
            not in TERMINAL_OPERATION_STATUSES
            and (
                not (
                    operation_run_id := str(
                        operation.get("workflow_run_id")
                        or operation.get("run_id")
                        or ""
                    ).strip()
                )
                or run_alias_map.get(operation_run_id, operation_run_id)
                not in terminal_runs
            )
        ]
        if active_operations:
            return False, "provider_operation_active", {
                "operation_ids": active_operations,
            }

        if not str(meta.get("provider_session_config_ref") or "") or not str(
            meta.get("provider_session_config_digest") or ""
        ):
            return False, "provider_session_config_unbound", {}
        provider_session = registry.get(role.instance_id)
        if provider_session is None:
            return False, "provider_session_identity_unproven", {}

        checkpoint_ref = str(heartbeat.get("checkpoint_ref") or "")
        if not checkpoint_ref:
            checkpoint_ref = _admitted_operation_checkpoint(
                operations,
                heartbeat=heartbeat,
                instance_id=role.instance_id,
            )
        workdir = (
            self.state_dir
            / "workdirs"
            / role.instance_id
            / "project"
        )
        if workdir.exists():
            dirty_files = capture_git_state(workdir).dirty_files
            managed_dirty_files = _managed_skill_dirty_files(
                state_dir=self.state_dir,
                project_root=self.project_root,
                workdir=workdir,
                instance_id=role.instance_id,
                dirty_files=dirty_files,
            )
            uncheckpointed_files = [
                path for path in dirty_files
                if path not in managed_dirty_files
            ]
            if uncheckpointed_files and not checkpoint_ref:
                return False, "workdir_dirty_without_checkpoint", {
                    "dirty_files": uncheckpointed_files,
                    "managed_dirty_files": managed_dirty_files,
                }
        return True, "eligible", {
            "idle_seconds": round(idle_seconds, 3),
            "checkpoint_ref": checkpoint_ref,
            "provider_session_id": str(provider_session),
        }

    def _runnable_task_ids_for_role(self, role: RoleConfig) -> list[str]:
        """Return backlog work that can mechanically target this role pool."""
        task_ids: list[str] = []
        for task in self.task_store.ready():
            evidence_contract = getattr(
                getattr(task, "contract", None),
                "evidence_contract",
                {},
            )
            if (
                isinstance(evidence_contract, dict)
                and evidence_contract.get("workflow_fanout_anchor") is True
            ):
                continue
            target = str(task.assigned_to or "").strip()
            if not target:
                target = str(self._initial_role_for_ready_task(task) or "").strip()
            if target:
                if target in {role.instance_id, role.name}:
                    task_ids.append(task.id)
                continue
            if (
                role.name != "orchestrator"
                and self._role_supports_task_skills(role, task)
            ):
                task_ids.append(task.id)
        return task_ids

    def _suspend_role(self, role: RoleConfig, *, now: float) -> None:
        registry = self._role_lifecycle_registry()
        registry.update_instance_meta(
            role.instance_id,
            lifecycle_state="suspending",
            lifecycle_transition_at=_iso_from_epoch(now),
        )
        self._set_worker_state(
            role.instance_id,
            "suspending",
            reason="on-demand provider idle threshold reached",
            force=True,
        )
        try:
            self.transport.terminate(role.instance_id)
        except Exception as exc:
            registry.update_instance_meta(
                role.instance_id,
                lifecycle_state="active",
                lifecycle_transition_at=_iso_from_epoch(self._now()),
                lifecycle_last_error=str(exc)[:500],
            )
            self._set_worker_state(
                role.instance_id,
                "idle",
                reason=f"on-demand provider suspend failed: {exc}",
                force=True,
            )
            self._record_suspend_rejection(
                role,
                reason="transport_terminate_failed",
                details={"error": str(exc)[:500]},
                now=self._now(),
            )
            return

        self._clear_role_skill_treatment(role.instance_id)

        completed = self._now()
        registry.update_instance_meta(
            role.instance_id,
            lifecycle_state="suspended",
            lifecycle_transition_at=_iso_from_epoch(completed),
            lifecycle_suspended_at=_iso_from_epoch(completed),
            lifecycle_last_error="",
        )
        registry.record_heartbeat(role.instance_id, {
            "instance_id": role.instance_id,
            "state": "suspended",
            "current_task_id": "",
            "last_action_ts": _iso_from_epoch(completed),
            "source": "role.lifecycle.suspended",
        })
        self._set_worker_state(
            role.instance_id,
            "suspended",
            reason="on-demand provider hibernated",
            force=True,
        )
        self._emit_role_lifecycle_event(
            "role.lifecycle.suspended",
            role,
            payload={
                "from": "suspending",
                "to": "suspended",
                "preserve_session": True,
                "preserve_workdir": True,
            },
        )

    def _record_suspend_rejection(
        self,
        role: RoleConfig,
        *,
        reason: str,
        details: dict[str, Any],
        now: float,
    ) -> None:
        registry = self._role_lifecycle_registry()
        meta = registry.instance_meta().get(role.instance_id, {})
        previous = str(meta.get("lifecycle_suspend_rejection") or "")
        previous_at = _epoch_from_value(
            meta.get("lifecycle_suspend_rejection_at")
        )
        if (
            previous == reason
            and previous_at is not None
            and now - previous_at < 60.0
        ):
            return
        registry.update_instance_meta(
            role.instance_id,
            lifecycle_suspend_rejection=reason,
            lifecycle_suspend_rejection_at=_iso_from_epoch(now),
        )
        self._emit_role_lifecycle_event(
            "role.lifecycle.suspend.rejected",
            role,
            payload={"reason": reason, **details},
        )

    def _latest_role_launch_is_resume(self, instance_id: str) -> bool:
        try:
            events = read_runtime_events(self.event_log, self.state_dir)
        except Exception:
            return False
        for event in reversed(events):
            if event.type != "worker.launch_artifact.written":
                continue
            payload = event.payload if isinstance(event.payload, dict) else {}
            if str(payload.get("instance_id") or "") == instance_id:
                return bool(payload.get("is_resume"))
        return False

    def _emit_role_lifecycle_event(
        self,
        event_type: str,
        role: RoleConfig,
        *,
        task_id: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> None:
        self.event_writer.append(ZfEvent(
            type=event_type,
            actor="orchestrator",
            task_id=task_id or None,
            payload={
                "schema_version": "role-lifecycle.v1",
                "role": role.name,
                "instance_id": role.instance_id,
                "backend": role.backend,
                **dict(payload or {}),
            },
            correlation_id=self._current_run_id(),
        ))


def _admitted_operation_checkpoint(
    operations: dict[str, dict[str, Any]],
    *,
    heartbeat: dict[str, Any],
    instance_id: str,
) -> str:
    operation_id = str(heartbeat.get("operation_id") or "").strip()
    operation = operations.get(operation_id) if operation_id else None
    if operation is None:
        role_operations = [
            item
            for item in operations.values()
            if str(item.get("role_instance") or "") == instance_id
        ]
        operation = role_operations[-1] if role_operations else None
    if not isinstance(operation, dict):
        return ""
    if str(operation.get("role_instance") or "") != instance_id:
        return ""
    if str(operation.get("status") or "") != "settled":
        return ""
    result_ref = operation.get("admitted_call_result_ref")
    if not isinstance(result_ref, dict) or not str(result_ref.get("ref") or ""):
        return ""
    operation_id = str(operation.get("operation_id") or operation_id)
    digest = str(result_ref.get("sha256") or result_ref.get("ref") or "")
    return f"workflow-operation://{operation_id}#{digest}"


def _managed_skill_dirty_files(
    *,
    state_dir: Path,
    project_root: Path,
    workdir: Path,
    instance_id: str,
    dirty_files: list[str],
) -> list[str]:
    manifest_path = (
        Path(state_dir)
        / "workdirs"
        / instance_id
        / "runtime"
        / "skills-manifest.json"
    )
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return []
    if str(manifest.get("instance_id") or "") != instance_id:
        return []
    managed_roots: list[str] = []
    for item in manifest.get("skills") or []:
        if not isinstance(item, dict):
            continue
        raw_path = str(item.get("materialized_to") or "").strip()
        if not raw_path:
            continue
        materialized_path = Path(raw_path)
        if not materialized_path.is_absolute():
            materialized_path = Path(project_root) / materialized_path
        try:
            relative = materialized_path.resolve().relative_to(workdir.resolve())
        except ValueError:
            continue
        parts = relative.parts
        if len(parts) < 3 or parts[0] not in {".claude", ".codex"}:
            continue
        if parts[1] != "skills":
            continue
        managed_roots.append(relative.as_posix().rstrip("/"))
    return [
        path
        for path in dirty_files
        if any(path == root or path.startswith(root + "/") for root in managed_roots)
    ]


def _epoch_from_value(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        pass
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def _iso_from_epoch(value: float) -> str:
    return datetime.fromtimestamp(value, tz=timezone.utc).isoformat()


def watchdog_should_skip(host: Any, role: RoleConfig) -> bool:
    """Keep expected inactive panes out of the generic death watchdog."""
    if role.lifecycle.mode != "on_demand":
        return False
    registry = RoleSessionRegistry(
        host.state_dir / "role_sessions.yaml",
        project_root=str(host.project_root),
    )
    state = str(
        registry.instance_meta().get(role.instance_id, {}).get(
            "lifecycle_state",
            "",
        )
    )
    if state not in _INACTIVE_LIFECYCLE_STATES:
        return False
    return (
        host._active_task_for_instance(role.instance_id) is None
        and host._active_fanout_child_for_instance(role.instance_id) is None
    )


def complete_respawn(host: Any, role: RoleConfig) -> None:
    """Close generic respawn bookkeeping and reactivate on-demand roles."""
    if role.lifecycle.mode == "on_demand":
        marker = getattr(host, "_mark_role_lifecycle_active", None)
        if callable(marker):
            marker(role)
    host._clear_respawn_failure(role.instance_id)


__all__ = [
    "RoleLifecycleRuntimeMixin",
    "complete_respawn",
    "watchdog_should_skip",
]
