"""Lifecycle evidence queries — K1 切片 1(2026-06-11)。

从 orchestrator_lifecycle.py verbatim 迁出的只读证据查询簇
(Task/Fanout 查询、Event/Manifest 提取、briefing 路径):零裁决、
零状态写入,供 watchdog/恢复/briefing 路径消费。迁移方式沿用仓库
split 先例(orchestrator → dispatch/lifecycle/reactor mixin),
方法体一字未改 —— 行为等价由 lifecycle 测试族 + 全量对照保证。
"""

from __future__ import annotations

from pathlib import Path

from zf.core.config.schema import RoleConfig
from zf.core.events.model import ZfEvent
from zf.core.events.segments import build_event_manifest
from zf.core.task.schema import Task
from zf.runtime.injection import infer_completion_protocol
from zf.runtime.run_admission import RUN_TERMINAL_EVENT_TYPES
from zf.runtime.run_scope import event_run_id, run_aliases


def _terminal_run_scope(
    events: list[ZfEvent],
) -> tuple[dict[str, str], dict[str, tuple[int, str, int]]]:
    """Return terminal epochs, including a bounded blocked-run reopen.

    A workflow may legally resume after ``run.goal.blocked`` by publishing a
    canonical active ``run.goal.updated``.  A terminal set loses that event
    ordering and makes every later fanout child look dead during watchdog
    recovery.  The tuple is ``(terminal_index, terminal_type, reopen_index)``;
    ``reopen_index`` remains ``-1`` for hard terminal outcomes.
    """
    aliases = run_aliases(events)
    known_runs = set(aliases.values())
    singleton = next(iter(known_runs)) if len(known_runs) == 1 else ""
    terminal_epochs: dict[str, tuple[int, str, int]] = {}
    for index, event in enumerate(events):
        run_id = event_run_id(event, aliases=aliases) or singleton
        if not run_id:
            continue
        if event.type in RUN_TERMINAL_EVENT_TYPES:
            terminal_epochs[run_id] = (index, event.type, -1)
            continue
        if event.type != "run.goal.updated":
            continue
        payload = event.payload if isinstance(event.payload, dict) else {}
        status = str(payload.get("status") or "").strip()
        epoch = terminal_epochs.get(run_id)
        if (
            status in {"active", "running"}
            and epoch is not None
            and epoch[1] == "run.goal.blocked"
            and index > epoch[0]
        ):
            terminal_epochs[run_id] = (epoch[0], epoch[1], index)
    return aliases, terminal_epochs


def _event_is_in_terminal_run_epoch(
    event: ZfEvent,
    *,
    event_index: int,
    aliases: dict[str, str],
    terminal_epochs: dict[str, tuple[int, str, int]],
) -> bool:
    run_id = event_run_id(event, aliases=aliases)
    epoch = terminal_epochs.get(run_id)
    if epoch is None:
        return False
    terminal_index, terminal_type, reopen_index = epoch
    if event_index < terminal_index:
        return True
    return not (
        terminal_type == "run.goal.blocked"
        and reopen_index > terminal_index
        and event_index > reopen_index
    )


class LifecycleEvidenceQueriesMixin:
    def _runtime_active_role_configs(self) -> list[RoleConfig]:
        roles = list(getattr(self.config, "roles", ()) or ())
        read_all = getattr(self.event_log, "read_all", None)
        if not callable(read_all):
            return roles

        from zf.runtime.flow_role_activation import (
            active_flow_role_instance_ids,
        )

        active = active_flow_role_instance_ids(self.config, read_all())
        try:
            from zf.core.state.role_sessions import RoleSessionRegistry

            lifecycle_meta = RoleSessionRegistry(
                self.state_dir / "role_sessions.yaml",
                project_root=str(self.project_root),
            ).instance_meta()
        except Exception:
            lifecycle_meta = {}
        return [
            role for role in roles
            if role.instance_id in active
            and not (
                str(getattr(getattr(role, "lifecycle", None), "mode", "") or "")
                == "on_demand"
                and str(
                    lifecycle_meta.get(role.instance_id, {}).get(
                        "lifecycle_state",
                        "dormant",
                    )
                    or "dormant"
                )
                in {"dormant", "suspended"}
            )
        ]

    def _fanout_child_briefing_path(
        self,
        role: "RoleConfig",
        fanout_child: dict,
    ) -> Path | None:
        raw_path = str(fanout_child.get("briefing_path") or "").strip()
        candidates: list[Path] = []
        if raw_path:
            candidate = Path(raw_path)
            if not candidate.is_absolute():
                candidate = self.project_root / candidate
            candidates.append(candidate)

        fanout_id = str(fanout_child.get("fanout_id") or "")
        child_id = str(fanout_child.get("child_id") or "")
        run_id = str(fanout_child.get("run_id") or "")
        if fanout_id and child_id:
            retry_first = (
                bool(fanout_child.get("retry_of_run_id"))
                or bool(fanout_child.get("attempt"))
                or "-retry-" in run_id
            )
            briefing_dir = self.state_dir / "briefings"
            initial = briefing_dir / f"{role.instance_id}-{fanout_id}-{child_id}.md"
            retry = briefing_dir / f"{role.instance_id}-{fanout_id}-{child_id}-retry.md"
            candidates.extend([retry, initial] if retry_first else [initial, retry])
            try:
                candidates.extend(
                    sorted(
                        briefing_dir.glob(
                            f"{role.instance_id}-{fanout_id}-{child_id}*.md"
                        )
                    )
                )
            except OSError:
                pass
        for candidate in candidates:
            try:
                if candidate.exists():
                    return candidate
            except OSError:
                continue
        return None


    def _active_task_for_instance(self, instance_id: str) -> Task | None:
        try:
            tasks = self.task_store.list_all()
        except Exception:
            return None
        task_by_id = {task.id: task for task in tasks}
        active_child = self._active_fanout_child_for_instance(instance_id)
        if active_child:
            child_task_id = str(active_child.get("task_id") or "")
            child_task = task_by_id.get(child_task_id)
            if child_task is not None and child_task.status == "in_progress":
                return child_task
        fanout_events = self._fanout_lifecycle_events()
        latest_dispatched = {}
        try:
            latest_dispatched = self._latest_dispatched_per_task()
        except Exception:
            latest_dispatched = {}
        for task in tasks:
            if task.status != "in_progress":
                continue
            if (
                self._fanout_task_state_for_instance(
                    instance_id,
                    task.id,
                    events=fanout_events,
                )
                == "terminal"
            ):
                continue
            assigned = task.assigned_to or ""
            dispatched = latest_dispatched.get(task.id, "")
            if assigned == instance_id or dispatched == instance_id:
                terminal = self._terminal_task_pipeline_assignments()
                terminal_dispatch = terminal.get((task.id, instance_id))
                if terminal_dispatch and terminal_dispatch == task.active_dispatch_id:
                    continue
                return task
        return None

    def _terminal_task_pipeline_assignments(self) -> dict[tuple[str, str], str]:
        """A Task may await integration after its provider operation has ended."""
        from zf.runtime.workflow_operation import (
            TERMINAL_OPERATION_STATUSES,
            reduce_workflow_operations,
        )

        cache_key = build_event_manifest(self.state_dir).digest
        cached = getattr(self, "_terminal_pipeline_assignments_cache", None)
        if cached is not None and cached[0] == cache_key:
            return cached[1]
        events = self.event_log.read_all()
        operations = reduce_workflow_operations(events)
        latest = {}
        for event in events:
            if event.type != "task.dispatched" or not event.task_id:
                continue
            payload = event.payload
            instance = str(payload.get("role_instance") or payload.get("assignee") or "")
            latest[(event.task_id, instance)] = payload
        active = {
            (str(row.get("task_id") or ""), str(row.get("role_instance") or ""))
            for row in operations.values()
            if row.get("task_pipeline_stage")
            and row.get("status") not in TERMINAL_OPERATION_STATUSES
        }
        terminal = {}
        for key, payload in latest.items():
            if not payload.get("task_pipeline_stage") or key in active:
                continue
            operation = operations.get(str(payload.get("operation_id") or ""), {})
            if operation.get("status") not in TERMINAL_OPERATION_STATUSES:
                continue
            if any(operation.get(field) != payload.get(field) for field in (
                "task_id", "role_instance", "dispatch_id", "task_pipeline_stage",
                "task_map_generation",
            )):
                continue
            terminal[key] = str(payload.get("dispatch_id") or "")
        self._terminal_pipeline_assignments_cache = (cache_key, terminal)
        return terminal

    def _fanout_task_state_for_instance(
        self,
        instance_id: str,
        task_id: str,
        *,
        events: list[ZfEvent] | None = None,
    ) -> str:
        """Return active/terminal for the latest fanout child of task/instance."""
        if not instance_id or not task_id:
            return ""
        if events is None:
            events = self._fanout_lifecycle_events()
        run_alias_map, terminal_epochs = _terminal_run_scope(events)
        terminal_fanouts: set[str] = set()
        stale_child_runs: set[tuple[str, str, str]] = set()
        for event_index in range(len(events) - 1, -1, -1):
            event = events[event_index]
            payload = event.payload if isinstance(event.payload, dict) else {}
            if event.type in {"fanout.cancelled", "fanout.timed_out"}:
                fanout_id = str(payload.get("fanout_id") or "")
                if fanout_id:
                    terminal_fanouts.add(fanout_id)
                continue
            if event.type not in {
                "fanout.child.dispatched",
                "fanout.child.completed",
                "fanout.child.failed",
                "fanout.child.dispatch_lost",
                "fanout.child.stale_completion",
            }:
                continue
            if str(payload.get("role_instance") or "").strip() != instance_id:
                continue
            event_task_id = str(
                event.task_id or payload.get("task_id") or ""
            ).strip()
            if event_task_id != task_id:
                continue
            if event.type == "fanout.child.stale_completion":
                key = self._fanout_child_key(payload)
                if key[0] and key[1]:
                    stale_child_runs.add(
                        key if key[2] else (key[0], key[1], "")
                    )
                continue
            if event.type == "fanout.child.dispatch_lost":
                return "terminal"
            if event.type == "fanout.child.dispatched":
                key = self._fanout_child_key(payload)
                if (
                    _event_is_in_terminal_run_epoch(
                        event,
                        event_index=event_index,
                        aliases=run_alias_map,
                        terminal_epochs=terminal_epochs,
                    )
                    or key in stale_child_runs
                    or (key[0], key[1], "") in stale_child_runs
                ):
                    return "terminal"
                if str(payload.get("fanout_id") or "") in terminal_fanouts:
                    return "terminal"
                return "active"
            return "terminal"
        return ""

    def _active_fanout_child_for_instance(self, instance_id: str) -> dict | None:
        """Return the latest non-terminal fanout work unit for a role instance.

        Reader fanout children intentionally do not create kanban tasks. For
        lifecycle decisions they and the aggregate synth operation still
        represent active work and must block idle recycle until a matching
        terminal event arrives.
        """
        events = self._fanout_lifecycle_events()
        run_alias_map, terminal_epochs = _terminal_run_scope(events)
        terminal_children: set[tuple[str, str, str]] = set()
        terminal_fanouts: set[str] = set()
        for event_index in range(len(events) - 1, -1, -1):
            event = events[event_index]
            payload = event.payload if isinstance(event.payload, dict) else {}
            if event.type == "fanout.child.dispatch_lost":
                key = self._fanout_child_key(payload)
                # Loss releases one dispatch, never a later retry of the child.
                if all(key):
                    terminal_children.add(key)
                continue
            if event.type == "fanout.child.stale_completion":
                key = self._fanout_child_key(payload)
                if key[0] and key[1]:
                    terminal_children.add(
                        key if key[2] else (key[0], key[1], "")
                    )
                continue
            if event.type in {
                "fanout.child.completed",
                "fanout.child.failed",
                "fanout.synth.completed",
            }:
                terminal_children.update(self._fanout_child_terminal_keys(payload))
                continue
            if event.type == "fanout.cancelled":
                # ZF-STOP-TAIL-01 邻居(07-16 实弹):被 supersede 取消的
                # fanout 其 child 曾被当作 active,recovery 反复给死 child
                # 重注简报,worker 完成申报在 flow 层永远无人承接。
                fanout_id = str(payload.get("fanout_id") or "")
                if fanout_id:
                    terminal_fanouts.add(fanout_id)
                continue
            if event.type == "fanout.timed_out":
                fanout_id = str(payload.get("fanout_id") or "")
                for child_id in payload.get("pending_children", []) or []:
                    child_id = str(child_id or "")
                    if fanout_id and child_id:
                        terminal_children.add((fanout_id, child_id, ""))
                continue
            if event.type not in {
                "fanout.child.dispatched",
                "fanout.synth.dispatched",
            }:
                continue
            if str(payload.get("role_instance") or "") != instance_id:
                continue
            if _event_is_in_terminal_run_epoch(
                event,
                event_index=event_index,
                aliases=run_alias_map,
                terminal_epochs=terminal_epochs,
            ):
                continue
            key = self._fanout_child_key(payload)
            if key[0] in terminal_fanouts:
                continue
            if (
                key in terminal_children
                or (key[0], key[1], "") in terminal_children
            ):
                continue
            return {
                "event_id": event.id,
                "fanout_id": key[0],
                "child_id": key[1],
                "run_id": key[2],
                "trace_id": str(payload.get("trace_id") or event.correlation_id or ""),
                "stage_id": str(payload.get("stage_id") or ""),
                "role_instance": instance_id,
                "briefing_path": str(payload.get("briefing_path") or ""),
                "snapshot_ref": str(payload.get("snapshot_ref") or ""),
                "task_id": str(payload.get("task_id") or event.task_id or ""),
                "retry_of_run_id": str(payload.get("retry_of_run_id") or ""),
                "attempt": payload.get("attempt"),
            }
        return None

    def _fanout_lifecycle_events(self) -> list[ZfEvent]:
        event_types = {
            "run.started",
            "run.goal.started",
            "run.goal.updated",
            "workflow.invoke.requested",
            *RUN_TERMINAL_EVENT_TYPES,
            "fanout.child.dispatched",
            "fanout.child.completed",
            "fanout.child.failed",
            "fanout.child.dispatch_lost",
            "fanout.child.stale_completion",
            "fanout.synth.dispatched",
            "fanout.synth.completed",
            # fanout 级终局:cancelled 使全体 child 失效;timed_out 此前
            # 虽有处理分支但从未被扫描命中(标记过滤先行)——一并补上。
            "fanout.cancelled",
            "fanout.timed_out",
        }
        cache_key = None
        try:
            cache_key = build_event_manifest(self.state_dir).digest
            cached = getattr(self, "_fanout_lifecycle_events_cache", None)
            if isinstance(cached, tuple) and cached[0] == cache_key:
                return list(cached[1])
        except (AttributeError, OSError):
            cache_key = None
        try:
            events = [
                event
                for event in self.event_log.read_all()
                if event.type in event_types
            ]
        except Exception:
            events = []
        if cache_key is not None:
            try:
                self._fanout_lifecycle_events_cache = (cache_key, list(events))
            except Exception:
                pass
        return events

    @staticmethod
    def _fanout_child_key(payload: dict) -> tuple[str, str, str]:
        return (
            str(payload.get("fanout_id") or ""),
            str(payload.get("child_id") or payload.get("child_run") or ""),
            str(payload.get("run_id") or ""),
        )

    @classmethod
    def _fanout_child_terminal_keys(
        cls,
        payload: dict,
    ) -> set[tuple[str, str, str]]:
        fanout_id, child_id, run_id = cls._fanout_child_key(payload)
        if not fanout_id or not child_id:
            return set()
        keys = {(fanout_id, child_id, "")}
        if run_id:
            keys.add((fanout_id, child_id, run_id))
        return keys

    def _latest_dispatch_event_for_task(self, task_id: str) -> ZfEvent | None:
        try:
            events = self.event_log.read_days(1)
        except Exception:
            return None
        for event in reversed(events):
            if event.type not in {"task.dispatched", "fanout.child.dispatched"}:
                continue
            payload = event.payload if isinstance(event.payload, dict) else {}
            dispatched_task_id = str(event.task_id or payload.get("task_id") or "")
            if dispatched_task_id == task_id:
                return event
        return None

    def _latest_unrejected_progress_event_for_dispatch(
        self,
        task_id: str,
        dispatch_id: str,
    ) -> ZfEvent | None:
        try:
            events = self.event_log.read_all()
        except Exception:
            return None
        dispatch_idx = -1
        latest: ZfEvent | None = None
        rejected_event_ids = self._rejected_origin_event_ids(events, task_id)
        for idx, event in enumerate(events):
            if event.task_id != task_id:
                continue
            payload = event.payload if isinstance(event.payload, dict) else {}
            if event.type == "task.dispatched":
                candidate_dispatch_id = str(payload.get("dispatch_id") or "")
                if (
                    not dispatch_id
                    or not candidate_dispatch_id
                    or candidate_dispatch_id == dispatch_id
                ):
                    dispatch_idx = idx
                continue
            if event.type not in self._STUCK_RECOVERY_PROGRESS_EVENTS:
                continue
            if dispatch_idx >= 0 and idx <= dispatch_idx:
                continue
            event_dispatch_id = str(payload.get("dispatch_id") or "")
            # Only exclude a progress event that itself carries a DIFFERENT
            # dispatch_id. Completion events like dev.build.done carry task_id
            # but no dispatch_id; the dispatch_idx guard above already scopes
            # them to the current dispatch, so an empty event_dispatch_id must
            # NOT be treated as a mismatch — otherwise a writer that finished
            # its build gets no progress credit, is flagged stuck, respawn-
            # cascades, and safe-halts the run (R17 dev-lane-4).
            if dispatch_id and event_dispatch_id and event_dispatch_id != dispatch_id:
                continue
            if event.id in rejected_event_ids:
                continue
            latest = event
        return latest

    def _rejected_origin_event_ids(
        self,
        events: list[ZfEvent],
        task_id: str,
    ) -> set[str]:
        rejected: set[str] = set()
        for event in events:
            if event.task_id != task_id:
                continue
            if event.type not in {
                "task.ref.rejected",
                "runtime.action.rejected",
                "dispatch.terminal.rejected",
            }:
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

    def _manifest_artifact_refs_for_prompt(self, event: ZfEvent) -> list[str]:
        payload = event.payload if isinstance(event.payload, dict) else {}
        manifest = payload.get("manifest") if isinstance(payload, dict) else None
        if not isinstance(manifest, dict):
            manifest = payload
        refs = manifest.get("artifact_refs") if isinstance(manifest, dict) else []
        out: list[str] = []
        if isinstance(refs, list):
            for item in refs[:12]:
                if not isinstance(item, dict):
                    continue
                kind = str(item.get("kind") or "artifact")
                path = str(item.get("path") or "")
                sha = str(item.get("sha256") or "")
                short_sha = sha[:12] if sha else "-"
                out.append(f"`{kind}` `{path}` sha256={short_sha}")
        return out

    def _expected_terminal_event_for_role(self, role: "RoleConfig") -> str:
        publishes = [
            str(event)
            for event in (getattr(role, "publishes", None) or [])
            if str(event) != "artifact.manifest.published"
        ]
        for suffix in (".approved", ".done", ".passed"):
            for event in publishes:
                if event.endswith(suffix):
                    return event
        if not publishes:
            return ""
        try:
            protocol = infer_completion_protocol(role)
            if (
                protocol.success_event != "artifact.manifest.published"
                and protocol.success_event in publishes
            ):
                return protocol.success_event
        except Exception:
            pass
        return ""

    @staticmethod
    def _event_dispatch_matches(payload: object, dispatch_id: str) -> bool:
        if not isinstance(payload, dict) or not dispatch_id:
            return True
        event_dispatch_id = str(payload.get("dispatch_id") or "")
        return not event_dispatch_id or event_dispatch_id == dispatch_id

    @staticmethod
    def _manifest_role_matches(event: ZfEvent, role: "RoleConfig") -> bool:
        payload = event.payload if isinstance(event.payload, dict) else {}
        manifest = payload.get("manifest") if isinstance(payload, dict) else None
        if not isinstance(manifest, dict):
            manifest = payload
        raw_role = ""
        if isinstance(manifest, dict):
            raw_role = str(
                manifest.get("role")
                or manifest.get("owner_role")
                or "",
            )
        if not raw_role:
            return True
        return raw_role in {role.name, role.instance_id}

    def _worker_state_after_progress_event(self, event: ZfEvent) -> str:
        if event.type == "dev.build.done":
            return "awaiting_review"
        if event.type == "dev.blocked":
            return "blocked_human"
        return "idle"

    def _pane_current_command(self, instance_id: str) -> str:
        getter = getattr(self.transport, "pane_current_command", None)
        if getter is None:
            return ""
        try:
            return str(getter(instance_id))
        except Exception:
            return ""

    def _latest_manifest_pending_terminal_event(
        self,
        *,
        task_id: str,
        dispatch_id: str,
        role: "RoleConfig",
        expected_event: str,
    ) -> ZfEvent | None:
        try:
            events = self.event_log.read_all()
        except Exception:
            return None
        rejected_event_ids = self._rejected_origin_event_ids(events, task_id)
        rejected_event_ids.update(
            self._rejected_artifact_manifest_event_ids(events, task_id)
        )
        dispatch_idx = -1
        manifest_idx = -1
        manifest_event: ZfEvent | None = None
        for idx, event in enumerate(events):
            if event.task_id != task_id:
                continue
            payload = event.payload if isinstance(event.payload, dict) else {}
            if event.type == "task.dispatched":
                candidate_dispatch_id = str(payload.get("dispatch_id") or "")
                if (
                    not dispatch_id
                    or not candidate_dispatch_id
                    or candidate_dispatch_id == dispatch_id
                ):
                    dispatch_idx = idx
                continue
            if dispatch_idx >= 0 and idx <= dispatch_idx:
                continue
            if event.type == "artifact.manifest.published":
                if event.id in rejected_event_ids:
                    continue
                if not self._event_dispatch_matches(payload, dispatch_id):
                    continue
                if not self._manifest_role_matches(event, role):
                    continue
                manifest_idx = idx
                manifest_event = event
                continue
            if (
                manifest_event is not None
                and idx > manifest_idx
                and event.type == expected_event
                and event.id not in rejected_event_ids
                and self._event_dispatch_matches(payload, dispatch_id)
            ):
                return None
        return manifest_event
