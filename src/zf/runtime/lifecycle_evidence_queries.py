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
from zf.core.task.schema import Task
from zf.runtime.injection import infer_completion_protocol


class LifecycleEvidenceQueriesMixin:
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
            if assigned == instance_id:
                return task
            dispatched = latest_dispatched.get(task.id, "")
            if dispatched == instance_id:
                return task
        return None

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
        for event in reversed(events):
            if event.type not in {
                "fanout.child.dispatched",
                "fanout.child.completed",
                "fanout.child.failed",
            }:
                continue
            payload = event.payload if isinstance(event.payload, dict) else {}
            if str(payload.get("role_instance") or "").strip() != instance_id:
                continue
            event_task_id = str(
                event.task_id or payload.get("task_id") or ""
            ).strip()
            if event_task_id != task_id:
                continue
            if event.type == "fanout.child.dispatched":
                return "active"
            return "terminal"
        return ""

    def _active_fanout_child_for_instance(self, instance_id: str) -> dict | None:
        """Return the latest non-terminal fanout child for a role instance.

        Reader fanout children intentionally do not create kanban tasks. For
        lifecycle decisions they still represent active work and must block
        idle recycle until a matching child terminal event arrives.
        """
        events = self._fanout_lifecycle_events()
        terminal_children: set[tuple[str, str, str]] = set()
        terminal_fanouts: set[str] = set()
        for event in reversed(events):
            payload = event.payload if isinstance(event.payload, dict) else {}
            if event.type in {"fanout.child.completed", "fanout.child.failed"}:
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
            if event.type != "fanout.child.dispatched":
                continue
            if str(payload.get("role_instance") or "") != instance_id:
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
            "fanout.child.dispatched",
            "fanout.child.completed",
            "fanout.child.failed",
            # fanout 级终局:cancelled 使全体 child 失效;timed_out 此前
            # 虽有处理分支但从未被扫描命中(标记过滤先行)——一并补上。
            "fanout.cancelled",
            "fanout.timed_out",
        }
        path = getattr(self.event_log, "path", None)
        cache_key = None
        if path is not None:
            try:
                stat = path.stat()
                cache_key = (stat.st_mtime_ns, stat.st_size)
                cached = getattr(self, "_fanout_lifecycle_events_cache", None)
                if isinstance(cached, tuple) and cached[0] == cache_key:
                    return list(cached[1])
            except OSError:
                cache_key = None
        events: list[ZfEvent] = []
        try:
            if path is None:
                raise OSError("event log path unavailable")
            markers = tuple(f'"type":"{event_type}"' for event_type in event_types)
            with path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    if not any(marker in line for marker in markers):
                        continue
                    try:
                        events.append(ZfEvent.from_json(line))
                    except Exception:
                        continue
        except Exception:
            try:
                events = [
                    event
                    for event in self.event_log.read_days(1)
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
