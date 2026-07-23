"""Resolve writer fanout result events to their canonical child manifest."""

from __future__ import annotations

from zf.core.events.model import ZfEvent
from zf.runtime.impl_self_check import (
    ImplSelfCheckError,
    descriptor_from_payload as self_check_descriptor_from_payload,
    hydrate_impl_self_check,
    normalize_impl_self_check,
    self_check_payload_fields,
    write_impl_self_check,
)
from zf.runtime.task_contract_snapshot import (
    TaskContractSnapshotError,
    build_target_snapshot,
    build_task_contract_snapshot,
    current_task_contract_identity,
    descriptor_from_payload,
    hydrate_target_snapshot,
    hydrate_task_contract_snapshot,
    snapshot_payload_fields,
    target_payload_fields,
    target_descriptor_from_payload,
    task_map_generation,
    write_target_snapshot,
    write_task_contract_snapshot,
)


class WriterFanoutResultBindingMixin:
    def _ensure_writer_completion_contract_identity(
        self,
        *,
        event: ZfEvent,
        payload: dict,
        child: dict,
        base_payload: dict,
    ) -> None:
        """Mint typed handoff snapshots for an adopted regular task attempt."""

        contract_input = {
            **(child.get("payload") if isinstance(child.get("payload"), dict) else {}),
            **child,
            **base_payload,
            **payload,
        }
        if not self._typed_task_contract_handoff_enabled(contract_input):
            return
        contract_snapshot_ready = bool(
            base_payload.get("contract_snapshot_ref")
            and base_payload.get("contract_snapshot_digest")
        )
        target_commit = str(
            payload.get("target_commit") or payload.get("source_commit") or ""
        ).strip()
        if not target_commit:
            raise TaskContractSnapshotError(
                "adopted writer completion lacks target commit for "
                f"{str(base_payload.get('task_id') or '').strip()}"
            )
        if contract_snapshot_ready:
            task_id = str(base_payload.get("task_id") or "").strip()
            task = self.task_store.get(task_id) if task_id else None
            if task is None:
                raise TaskContractSnapshotError(
                    f"cannot validate missing canonical task {task_id!r}"
                )
            snapshot = hydrate_task_contract_snapshot(
                self.state_dir,
                descriptor_from_payload(base_payload),
                expected=current_task_contract_identity(
                    task,
                    task_map_ref=str(base_payload.get("task_map_ref") or ""),
                ),
            )
            target_snapshot = build_target_snapshot(
                descriptor_from_payload(base_payload),
                target_commit=target_commit,
                contract_snapshot=snapshot,
            )
            try:
                target_descriptor = target_descriptor_from_payload(base_payload)
                target_snapshot = hydrate_target_snapshot(
                    self.state_dir,
                    target_descriptor,
                    expected={
                        "contract_snapshot_ref": str(
                            base_payload.get("contract_snapshot_ref") or ""
                        ),
                        "contract_snapshot_digest": str(
                            base_payload.get("contract_snapshot_digest") or ""
                        ),
                        "target_commit": target_commit,
                    },
                )
            except TaskContractSnapshotError:
                target_descriptor = write_target_snapshot(
                    self.state_dir,
                    target_snapshot,
                    source_event_id=event.id,
                )
            base_payload.update({
                "target_commit": target_commit,
                **target_payload_fields(target_descriptor),
            })
            self._ensure_impl_self_check_handoff(
                event=event,
                payload=payload,
                base_payload=base_payload,
                contract_snapshot=snapshot,
                target_snapshot=target_snapshot,
            )
            return
        task_id = str(base_payload.get("task_id") or "").strip()
        task = self.task_store.get(task_id) if task_id else None
        if task is None:
            raise TaskContractSnapshotError(
                f"cannot snapshot missing canonical task {task_id!r}"
            )
        dispatch_id = str(
            payload.get("dispatch_id") or payload.get("attempt_id") or ""
        ).strip()
        base_commit = str(
            payload.get("base_commit") or base_payload.get("base_commit") or ""
        ).strip()
        if not base_commit and dispatch_id:
            try:
                dispatch_event = next(
                    item for item in reversed(self.event_log.read_all())
                    if item.type == "task.dispatched"
                    and str((item.payload or {}).get("dispatch_id") or "") == dispatch_id
                    and str(item.task_id or "") == task_id
                )
            except (StopIteration, OSError):
                dispatch_event = None
            if dispatch_event is not None and isinstance(dispatch_event.payload, dict):
                base_commit = str(
                    dispatch_event.payload.get("base_git_head") or ""
                ).strip()
        workflow_run_id = str(
            base_payload.get("workflow_run_id") or event.correlation_id or ""
        ).strip()
        if not base_commit:
            raise TaskContractSnapshotError(
                f"adopted writer completion lacks dispatch base commit for {task_id}"
            )
        snapshot = build_task_contract_snapshot(
            task,
            workflow_run_id=workflow_run_id,
            task_map_generation_id=task_map_generation(
                task,
                task_map_ref=str(base_payload.get("task_map_ref") or ""),
            ),
            base_commit=base_commit,
            task_ref=f"{self.config.runtime.git.task_ref_prefix}/{task_id}",
        )
        descriptor = write_task_contract_snapshot(
            self.state_dir,
            snapshot,
            source_event_id=event.id,
        )
        target_descriptor = write_target_snapshot(
            self.state_dir,
            build_target_snapshot(
                descriptor,
                target_commit=target_commit,
                contract_snapshot=snapshot,
            ),
            source_event_id=event.id,
        )
        base_payload.update({
            "workflow_run_id": str(snapshot["workflow_run_id"]),
            "contract_revision": str(snapshot["contract_revision"]),
            "task_map_generation": str(snapshot["task_map_generation"]),
            "base_commit": str(snapshot["base_commit"]),
            "target_commit": target_commit,
            **snapshot_payload_fields(descriptor),
            **target_payload_fields(target_descriptor),
        })
        self._ensure_impl_self_check_handoff(
            event=event,
            payload=payload,
            base_payload=base_payload,
            contract_snapshot=snapshot,
            target_snapshot=build_target_snapshot(
                descriptor,
                target_commit=target_commit,
                contract_snapshot=snapshot,
            ),
        )

    def _ensure_impl_self_check_handoff(
        self,
        *,
        event: ZfEvent,
        payload: dict,
        base_payload: dict,
        contract_snapshot: dict,
        target_snapshot: dict,
    ) -> None:
        """Admit or recover one exact-target Agent self-check sidecar."""

        required = bool(getattr(
            getattr(self.config, "workflow", None),
            "impl_self_check_required",
            False,
        ))
        descriptor_payload: dict = {}
        for source in (base_payload, payload):
            if source.get("impl_self_check_ref") and source.get("impl_self_check_digest"):
                descriptor_payload = source
                break
        if not descriptor_payload:
            target_commit = str(target_snapshot.get("target_commit") or "")
            try:
                prior = next(
                    item
                    for item in reversed(self.event_log.read_all())
                    if item.type == "impl.self_check.completed"
                    and str(item.task_id or "") == str(base_payload.get("task_id") or "")
                    and isinstance(item.payload, dict)
                    and str(item.payload.get("target_commit") or "") == target_commit
                )
            except (StopIteration, OSError):
                prior = None
            if prior is not None:
                descriptor_payload = prior.payload
        if descriptor_payload:
            descriptor = self_check_descriptor_from_payload(descriptor_payload)
            hydrate_impl_self_check(
                self.state_dir,
                descriptor,
                contract_snapshot=contract_snapshot,
                target_snapshot=target_snapshot,
            )
            base_payload.update(self_check_payload_fields(descriptor))
            return

        if isinstance(payload.get("impl_self_check"), dict):
            attempt_id = str(
                payload.get("attempt_id") or payload.get("dispatch_id") or ""
            ).strip()
            body = normalize_impl_self_check(
                payload,
                contract_snapshot=contract_snapshot,
                target_snapshot=target_snapshot,
                expected_attempt_id=attempt_id,
                strict=True,
            )
            descriptor = write_impl_self_check(
                self.state_dir,
                body,
                source_event_id=event.id,
                created_by=str(event.actor or "worker"),
            )
            fields = self_check_payload_fields(descriptor)
            base_payload.update(fields)
            self.event_writer.append(ZfEvent(
                type="impl.self_check.completed",
                actor="orchestrator",
                task_id=str(base_payload.get("task_id") or ""),
                payload={
                    **fields,
                    "workflow_run_id": str(contract_snapshot.get("workflow_run_id") or ""),
                    "contract_revision": str(contract_snapshot.get("contract_revision") or ""),
                    "target_commit": str(target_snapshot.get("target_commit") or ""),
                    "attempt_id": str(body.get("attempt_id") or ""),
                },
                causation_id=event.id,
                correlation_id=event.correlation_id,
            ))
            return
        if required:
            raise ImplSelfCheckError(
                "writer completion lacks required impl_self_check sidecar"
            )

    def _writer_source_event_already_terminal(self, source_event_id: str) -> bool:
        """Return whether one writer result was already terminally consumed.

        Fanout replacement may adopt a genuinely late result exactly once. A
        durable result that already completed or failed an older child must
        not be replayed into every later generation.
        """
        if not source_event_id:
            return False
        try:
            events = self.event_log.read_all()
        except OSError:
            return False
        for recorded in reversed(events):
            if recorded.type not in {
                "fanout.child.completed",
                "fanout.child.failed",
            }:
                continue
            recorded_payload = (
                recorded.payload if isinstance(recorded.payload, dict) else {}
            )
            if source_event_id in {
                str(recorded_payload.get("result_event_id") or ""),
                str(recorded.causation_id or ""),
            }:
                return True
        return False

    def _writer_completion_identity_already_terminal(self, event: ZfEvent) -> bool:
        """Return whether an equivalent writer completion was already consumed."""
        if event.type != "dev.build.done":
            return False
        payload = self._fanout_result_payload(event)
        fanout_id = str(payload.get("fanout_id") or "")
        child_id = str(payload.get("child_id") or payload.get("child_run") or "")
        run_id = str(payload.get("run_id") or "")
        source_commit = str(payload.get("source_commit") or "")
        if not fanout_id or not child_id or not source_commit:
            return False
        try:
            events = self.event_log.read_all()
        except OSError:
            return False
        for recorded in reversed(events):
            if recorded.type not in {"fanout.child.completed", "fanout.child.failed"}:
                continue
            recorded_payload = (
                recorded.payload if isinstance(recorded.payload, dict) else {}
            )
            if str(recorded_payload.get("result_event_id") or "") == event.id:
                continue
            if str(recorded_payload.get("fanout_id") or "") != fanout_id:
                continue
            if str(recorded_payload.get("child_id") or "") != child_id:
                continue
            if run_id and str(recorded_payload.get("run_id") or "") != run_id:
                continue
            if str(recorded_payload.get("source_commit") or "") != source_commit:
                continue
            return True
        return False

    def _fanout_result_payload(self, event: ZfEvent) -> dict:
        payload = event.payload if isinstance(event.payload, dict) else {}
        report = payload.get("report")
        promoted = dict(payload)
        if isinstance(report, dict):
            for key in (
                "fanout_id",
                "stage_id",
                "child_id",
                "child_run",
                "run_id",
                "role_instance",
                "status",
                "reason",
                "summary",
                "recommendation",
            ):
                if promoted.get(key) in (None, "") and report.get(key) not in (None, ""):
                    promoted[key] = report.get(key)
        if event.type in {"task.ref.updated", "task.ref.rejected"} and not promoted.get(
            "fanout_id"
        ):
            trigger_event_id = str(
                promoted.get("trigger_event_id") or event.causation_id or ""
            )
            source_event = None
            if trigger_event_id:
                try:
                    source_event = next(
                        item
                        for item in reversed(self.event_log.read_all())
                        if item.id == trigger_event_id
                    )
                except (StopIteration, OSError):
                    source_event = None
            if source_event is not None and source_event.type == "dev.build.done":
                source_payload = (
                    source_event.payload if isinstance(source_event.payload, dict) else {}
                )
                for key in (
                    "fanout_id",
                    "stage_id",
                    "child_id",
                    "child_run",
                    "run_id",
                    "dispatch_id",
                    "role_instance",
                    "task_map_ref",
                    "source_index_ref",
                    "pdd_id",
                    "feature_id",
                ):
                    value = source_payload.get(key)
                    if value not in (None, ""):
                        promoted.setdefault(key, value)
                promoted.setdefault("role_instance", source_event.actor or "")
        if promoted.get("fanout_id") and (
            promoted.get("child_id") or promoted.get("child_run")
        ):
            return promoted
        target = self._writer_fanout_completion_target(
            event=event,
            payload=promoted,
            current_fanout_id="",
            statuses={"dispatched", "failed", "completed"},
        )
        adopted_dispatch_id = ""
        if target is None:
            fenced_target = self._writer_fanout_fenced_pending_target(
                event=event,
                payload=promoted,
            )
            if fenced_target is not None:
                manifest, child, adopted_dispatch_id = fenced_target
                target = (manifest, child)
        if target is None:
            return promoted
        manifest, child = target
        enriched = dict(promoted)
        enriched.setdefault("fanout_id", str(manifest.get("fanout_id") or ""))
        enriched.setdefault("stage_id", str(manifest.get("stage_id") or ""))
        enriched.setdefault("trace_id", str(manifest.get("trace_id") or ""))
        enriched.setdefault("child_id", str(child.get("child_id") or ""))
        enriched.setdefault("run_id", str(child.get("run_id") or ""))
        enriched.setdefault("role_instance", str(child.get("role_instance") or ""))
        enriched.setdefault("task_id", str(child.get("task_id") or ""))
        enriched.setdefault(
            "task_map_ref",
            str(child.get("task_map_ref") or manifest.get("task_map_ref") or ""),
        )
        enriched.setdefault(
            "source_index_ref",
            str(child.get("source_index_ref") or manifest.get("source_index_ref") or ""),
        )
        enriched.setdefault("pdd_id", str(manifest.get("pdd_id") or ""))
        enriched.setdefault("feature_id", str(manifest.get("feature_id") or ""))
        enriched.setdefault("scope", str(child.get("scope") or ""))
        enriched.setdefault("workdir", str(child.get("workdir") or ""))
        enriched.setdefault("source_branch", str(child.get("source_branch") or ""))
        if adopted_dispatch_id:
            enriched["_writer_fanout_adopted_dispatch_id"] = adopted_dispatch_id
            enriched["_writer_fanout_adoption_reason"] = "active_task_attempt_fence"
        return enriched

    def _writer_fanout_fenced_pending_target(
        self,
        *,
        event: ZfEvent,
        payload: dict,
    ) -> tuple[dict, dict, str] | None:
        """Adopt one regular task attempt that raced a writer fanout.

        A task-map update can make a task dispatchable immediately before the
        writer fanout has claimed it. The single-writer fence correctly keeps
        the fanout child pending, but the regular completion still belongs to
        that exact task generation. Bind only when the ledger proves one
        latest regular dispatch and one current pending child with the same
        task, role, and task-map identity.
        """
        if event.type not in {
            "dev.build.done",
            "dev.blocked",
            "dev.failed",
            "task.ref.updated",
            "task.ref.rejected",
        }:
            return None
        task_id = str(event.task_id or payload.get("task_id") or "").strip()
        dispatch_id = str(
            payload.get("dispatch_id") or payload.get("attempt_id") or ""
        ).strip()
        if not task_id or not dispatch_id:
            return None
        try:
            events = self.event_log.read_all()
        except OSError:
            return None

        latest_dispatch = None
        for item in events:
            if item.type != "task.dispatched":
                continue
            item_payload = item.payload if isinstance(item.payload, dict) else {}
            item_task_id = str(item.task_id or item_payload.get("task_id") or "")
            if item_task_id == task_id:
                latest_dispatch = item
        if latest_dispatch is None:
            return None
        dispatch_payload = (
            latest_dispatch.payload
            if isinstance(latest_dispatch.payload, dict)
            else {}
        )
        if str(dispatch_payload.get("dispatch_id") or "") != dispatch_id:
            return None
        dispatch_role = str(
            dispatch_payload.get("assignee") or dispatch_payload.get("role") or ""
        ).strip()
        event_role = str(
            payload.get("role_instance")
            or (event.actor if event.type not in {"task.ref.updated", "task.ref.rejected"} else "")
            or ""
        ).strip()
        if not dispatch_role or (event_role and event_role != dispatch_role):
            return None
        task = self.task_store.get(task_id)
        active_dispatch_id = str(
            getattr(task, "active_dispatch_id", "") or ""
        ) if task is not None else ""
        if active_dispatch_id and active_dispatch_id != dispatch_id:
            return None

        started_fanout_ids = {
            str((item.payload or {}).get("fanout_id") or "")
            for item in events
            if item.type == "fanout.started" and isinstance(item.payload, dict)
        }
        event_task_map_ref = str(payload.get("task_map_ref") or "").strip()
        root = self.state_dir / "fanouts"
        if not root.exists():
            return None
        candidates: list[tuple[dict, dict]] = []
        for manifest_path in root.glob("*/manifest.json"):
            fanout_id = manifest_path.parent.name
            if fanout_id not in started_fanout_ids:
                continue
            manifest = self._fanout_manifest(fanout_id)
            if not manifest or manifest.get("topology") != "fanout_writer_scoped":
                continue
            stale_reason, _ = self._fanout_identity_stale_reason(fanout_id)
            if stale_reason:
                continue
            for child in manifest.get("children", []) or []:
                if not isinstance(child, dict):
                    continue
                if str(child.get("status") or "") != "pending":
                    continue
                if str(child.get("task_id") or "") != task_id:
                    continue
                if str(child.get("role_instance") or "") != dispatch_role:
                    continue
                child_task_map_ref = str(
                    child.get("task_map_ref") or manifest.get("task_map_ref") or ""
                ).strip()
                if (
                    event_task_map_ref
                    and child_task_map_ref
                    and event_task_map_ref != child_task_map_ref
                ):
                    continue
                candidates.append((manifest, child))
        if len(candidates) != 1:
            return None
        manifest, child = candidates[0]
        return manifest, child, dispatch_id

    def _writer_fanout_completion_target(
        self,
        *,
        event: ZfEvent,
        payload: dict,
        current_fanout_id: str,
        statuses: set[str],
    ) -> tuple[dict, dict] | None:
        if event.type not in {
            "dev.build.done",
            "dev.blocked",
            "dev.failed",
            "task.ref.updated",
            "task.ref.rejected",
        }:
            return None
        task_id = str(event.task_id or payload.get("task_id") or "").strip()
        if not task_id:
            return None
        event_dispatch_id = str(
            payload.get("dispatch_id")
            or payload.get("attempt_id")
            or payload.get("run_id")
            or ""
        ).strip()
        event_task_map_ref = str(payload.get("task_map_ref") or "").strip()
        role_instance = str(
            payload.get("role_instance")
            or (
                payload.get("actor")
                if event.type in {"task.ref.updated", "task.ref.rejected"}
                else None
            )
            or (
                event.actor
                if event.type not in {"task.ref.updated", "task.ref.rejected"}
                else None
            )
            or ""
        ).strip()
        root = self.state_dir / "fanouts"
        if not root.exists():
            return None
        candidates: list[tuple[float, dict, dict]] = []
        for manifest_path in root.glob("*/manifest.json"):
            fanout_id = manifest_path.parent.name
            manifest = self._fanout_manifest(fanout_id)
            if not manifest or manifest.get("topology") != "fanout_writer_scoped":
                continue
            for child in manifest.get("children", []) or []:
                if not isinstance(child, dict):
                    continue
                if str(child.get("task_id") or "") != task_id:
                    continue
                status = str(child.get("status") or "")
                if status not in statuses:
                    continue
                child_run_id = str(child.get("run_id") or "").strip()
                rework_lineage_matches = (
                    status == "failed"
                    and self._writer_rework_dispatch_reaches_child_run(
                        event=event,
                        payload=payload,
                        child_run_id=child_run_id,
                    )
                )
                if (
                    event_dispatch_id
                    and child_run_id
                    and event_dispatch_id != child_run_id
                    and not (
                        status == "completed"
                        and self._writer_fanout_completed_child_repair_allowed(
                            event=event,
                            payload=payload,
                            child=child,
                        )
                    )
                    and not rework_lineage_matches
                ):
                    continue
                child_role = str(child.get("role_instance") or "")
                if role_instance and child_role and role_instance != child_role:
                    continue
                child_task_map_ref = str(
                    child.get("task_map_ref") or manifest.get("task_map_ref") or ""
                ).strip()
                if (
                    event_task_map_ref
                    and child_task_map_ref
                    and event_task_map_ref != child_task_map_ref
                ):
                    continue
                try:
                    mtime = manifest_path.stat().st_mtime
                except OSError:
                    mtime = 0.0
                if status == "dispatched":
                    mtime += 1000.0
                if fanout_id == current_fanout_id:
                    mtime += 1_000_000_000.0
                candidates.append((mtime, manifest, child))
        if not candidates:
            return None
        _, manifest, child = sorted(candidates, key=lambda item: item[0])[-1]
        return manifest, child

    def _writer_rework_dispatch_reaches_child_run(
        self,
        *,
        event: ZfEvent,
        payload: dict,
        child_run_id: str,
    ) -> bool:
        """Prove that a task-ref repair descends from one failed child.

        Task-ref repair uses ordinary task dispatch ids. After more than one
        repair, the completion no longer carries the original fanout run id,
        but each ``task.rework.requested`` records its predecessor in
        ``base_dispatch_id``. Follow only that kernel-emitted chain; matching
        task and role checks still run in ``_writer_fanout_completion_target``.
        """
        if event.type not in {"dev.build.done", "task.ref.updated"}:
            return False
        task_id = str(event.task_id or payload.get("task_id") or "").strip()
        current_dispatch_id = str(
            payload.get("dispatch_id")
            or payload.get("attempt_id")
            or payload.get("run_id")
            or ""
        ).strip()
        if not task_id or not current_dispatch_id or not child_run_id:
            return False
        try:
            events = self.event_log.read_all()
        except OSError:
            return False
        events_by_id = {item.id: item for item in events}
        dispatches = {
            str((item.payload or {}).get("dispatch_id") or ""): item
            for item in events
            if item.type == "task.dispatched"
            and item.task_id == task_id
            and isinstance(item.payload, dict)
            and (item.payload or {}).get("dispatch_id")
        }
        visited: set[str] = set()
        for _ in range(16):
            if current_dispatch_id == child_run_id:
                return True
            if current_dispatch_id in visited:
                return False
            visited.add(current_dispatch_id)
            dispatch = dispatches.get(current_dispatch_id)
            if dispatch is None:
                return False
            dispatch_payload = dispatch.payload
            if (
                dispatch_payload.get("source") != "rework"
                or dispatch_payload.get("trigger_event")
                != "task.ref.repair.requested"
            ):
                return False
            request_id = str(
                dispatch_payload.get("rework_request_event_id")
                or dispatch.causation_id
                or ""
            ).strip()
            request = events_by_id.get(request_id)
            if (
                request is None
                or request.type != "task.rework.requested"
                or request.task_id != task_id
                or not isinstance(request.payload, dict)
            ):
                return False
            current_dispatch_id = str(
                request.payload.get("base_dispatch_id") or ""
            ).strip()
            if not current_dispatch_id:
                return False
        return False


__all__ = ["WriterFanoutResultBindingMixin"]
