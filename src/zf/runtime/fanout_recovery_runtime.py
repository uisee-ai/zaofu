"""Recovery helpers shared by reader and writer fanout coordination."""

from __future__ import annotations

import json

from zf.core.events.model import ZfEvent


def recover_unrecorded_reader_fanout_results(runtime) -> None:
    """Reconcile reader results in restart-safe, cheapest-first order."""

    from zf.runtime.reader_fanout_recovery_snapshot import (
        build_reader_fanout_recovery_snapshot,
        event_log_snapshot_token,
    )

    try:
        from zf.runtime.event_window import read_runtime_events

        events = read_runtime_events(runtime.event_log, runtime.state_dir)
    except Exception:
        return
    event_token = event_log_snapshot_token(runtime.event_log)
    runtime._recover_durable_fanout_aggregate_results(events)
    if event_log_snapshot_token(runtime.event_log) != event_token:
        try:
            events = read_runtime_events(runtime.event_log, runtime.state_dir)
        except Exception:
            return
    if runtime._resume_unstarted_reader_fanouts(events):
        try:
            events = read_runtime_events(runtime.event_log, runtime.state_dir)
        except Exception:
            return
    if runtime._resume_unrecorded_reader_synth_results(events):
        try:
            events = read_runtime_events(runtime.event_log, runtime.state_dir)
        except Exception:
            return

    recovery_snapshot = build_reader_fanout_recovery_snapshot(runtime, events)
    if not recovery_snapshot.consistent:
        return
    if not has_nonterminal_reader_fanouts(
        runtime,
        recovery_snapshot=recovery_snapshot,
    ):
        return
    for recover in (
        # A durable worker result may be queued behind provider.turn.closed.
        # Admit/replay it before deciding that the provider lost its dispatch.
        runtime._resume_unrecorded_reader_fanout_results,
        runtime._recover_pending_reader_fanout_dispatches,
        runtime._recover_lost_reader_fanout_dispatches,
        runtime._recover_lost_fanout_synth_dispatches,
    ):
        if recover(events, recovery_snapshot=recovery_snapshot):
            # One immutable event/manifest snapshot owns one recovery sweep.
            # A mutation is a new snapshot boundary and is picked up by the
            # next level-triggered tick instead of rescanning every manifest.
            return
    runtime._recover_incomplete_reader_fanout_aggregates(
        events,
        recovery_snapshot=recovery_snapshot,
    )


def has_nonterminal_reader_fanouts(
    runtime,
    *,
    recovery_snapshot=None,
) -> bool:
    terminal = {"completed", "failed", "timed_out", "cancelled"}
    if recovery_snapshot is not None:
        manifests = recovery_snapshot.manifests
    else:
        fanout_root = runtime.state_dir / "fanouts"
        if not fanout_root.exists():
            return False
        manifests = (
            (path.parent.name, runtime._fanout_manifest(path.parent.name))
            for path in fanout_root.glob("*/manifest.json")
        )
    for _fanout_id, manifest in manifests:
        if not manifest or manifest.get("topology") != "fanout_reader":
            continue
        aggregate = (
            manifest.get("aggregate")
            if isinstance(manifest.get("aggregate"), dict)
            else {}
        )
        if (
            str(manifest.get("status") or "") not in terminal
            and str(aggregate.get("status") or "") not in terminal
        ):
            return True
    return False


def task_has_active_writer_fanout_dispatch(runtime, task_id: str) -> bool:
    task = runtime.task_store.get(task_id)
    if task is None or not str(task.active_dispatch_id or ""):
        return False
    active_dispatch_id = str(task.active_dispatch_id)
    fanout_root = runtime.state_dir / "fanouts"
    if not fanout_root.exists():
        return False
    for manifest_path in fanout_root.glob("*/manifest.json"):
        manifest = runtime._fanout_manifest(manifest_path.parent.name)
        if not manifest or manifest.get("topology") != "fanout_writer_scoped":
            continue
        aggregate = (
            manifest.get("aggregate")
            if isinstance(manifest.get("aggregate"), dict)
            else {}
        )
        if aggregate.get("status") in {
            "completed",
            "failed",
            "timed_out",
            "cancelled",
        }:
            continue
        for child in manifest.get("children", []) or []:
            if not isinstance(child, dict):
                continue
            if (
                str(child.get("status") or "") == "dispatched"
                and str(child.get("task_id") or "") == task_id
                and str(child.get("run_id") or "") == active_dispatch_id
            ):
                return True
    return False


def reader_result_replay_events(
    runtime,
    events: list[ZfEvent],
    *,
    terminal_children: set[tuple[str, str]],
    recovery_snapshot=None,
) -> list[ZfEvent]:
    """Bound result replay to each unresolved child's durable dispatch."""

    event_order = (
        recovery_snapshot.event_order
        if recovery_snapshot is not None
        else {event.id: index for index, event in enumerate(events)}
    )
    terminal_statuses = {"completed", "failed", "timed_out", "cancelled"}
    boundaries: list[int] = []
    unresolved_children = 0
    if recovery_snapshot is not None:
        manifests = recovery_snapshot.manifests
    else:
        fanout_root = runtime.state_dir / "fanouts"
        if not fanout_root.exists():
            return []
        manifests = (
            (path.parent.name, runtime._fanout_manifest(path.parent.name))
            for path in fanout_root.glob("*/manifest.json")
        )

    for fanout_id, manifest in manifests:
        if not manifest or manifest.get("topology") != "fanout_reader":
            continue
        aggregate = (
            manifest.get("aggregate")
            if isinstance(manifest.get("aggregate"), dict)
            else {}
        )
        if (
            str(manifest.get("status") or "") in terminal_statuses
            or str(aggregate.get("status") or "") in terminal_statuses
        ):
            continue
        for child in manifest.get("children", []) or []:
            if not isinstance(child, dict):
                continue
            child_id = str(child.get("child_id") or "")
            if not child_id:
                return events
            if (
                str(child.get("status") or "") in {"completed", "failed"}
                and (fanout_id, child_id) in terminal_children
            ):
                continue
            unresolved_children += 1
            boundary_id = str(child.get("last_event_id") or "")
            boundary_index = event_order.get(boundary_id)
            if boundary_index is None:
                return events
            boundaries.append(boundary_index)

    if unresolved_children == 0:
        return []
    return events[min(boundaries) + 1:]


def reader_fanout_superseding_goal_claim(
    events: list[ZfEvent],
    *,
    manifest: dict,
) -> str:
    """Return the admitted Goal claim that makes a reader fanout stale."""

    run_id = str(
        manifest.get("workflow_run_id")
        or manifest.get("trace_id")
        or ""
    ).strip()
    goal_keys = {
        str(value).strip()
        for value in (
            manifest.get("pdd_id"),
            manifest.get("feature_id"),
        )
        if str(value or "").strip()
    }
    rejected_claim_ids = {
        str((event.payload or {}).get("claim_id") or "").strip()
        for event in events
        if event.type == "run.goal.completion.rejected"
        and isinstance(event.payload, dict)
    }
    for event in reversed(events):
        if event.type != "run.goal.completion.claimed":
            continue
        payload = event.payload if isinstance(event.payload, dict) else {}
        if str(payload.get("claim_type") or "") != (
            "admitted_goal_closure_result"
        ):
            continue
        claim_id = str(payload.get("claim_id") or event.id).strip()
        if not claim_id or claim_id in rejected_claim_ids:
            continue
        claim_run_id = str(
            payload.get("workflow_run_id")
            or payload.get("run_id")
            or event.correlation_id
            or ""
        ).strip()
        if run_id and claim_run_id and run_id != claim_run_id:
            continue
        claim_goal_keys = {
            str(value).strip()
            for value in (
                payload.get("goal_id"),
                payload.get("pdd_id"),
                payload.get("feature_id"),
            )
            if str(value or "").strip()
        }
        if goal_keys and claim_goal_keys and goal_keys.isdisjoint(
            claim_goal_keys
        ):
            continue
        if not (
            (run_id and claim_run_id)
            or (goal_keys and claim_goal_keys)
        ):
            continue
        return claim_id
    return ""


class FanoutRecoveryRuntimeMixin:
    def _publish_writer_fanout_task_capsule(
        self,
        *,
        task_id: str,
        dispatch_id: str,
    ) -> None:
        """Keep the task capsule aligned with a canonical writer binding."""

        task = self.task_store.get(task_id)
        if task is None:
            return
        from zf.runtime.task_doc import (
            task_doc_dir,
            verify_task_capsule,
            write_task_doc,
        )

        preflight_errors = verify_task_capsule(self.state_dir, task)
        try:
            manifest = json.loads(
                (task_doc_dir(self.state_dir, task_id) / "manifest.json").read_text(
                    encoding="utf-8",
                )
            )
        except (OSError, json.JSONDecodeError):
            manifest = {}
        if (
            not preflight_errors
            and str(manifest.get("assigned_to") or "") == str(task.assigned_to or "")
            and str(manifest.get("active_dispatch_id") or "")
            == str(task.active_dispatch_id or "")
            and str(manifest.get("dispatch_id") or "") == dispatch_id
        ):
            return

        task_doc = write_task_doc(
            self.state_dir,
            task,
            dispatch_id=dispatch_id,
            project_root=self.project_root,
        )
        preflight_errors = verify_task_capsule(self.state_dir, task)
        if preflight_errors:
            raise RuntimeError(
                "writer fanout task capsule preflight failed: "
                + ", ".join(preflight_errors)
            )
        from zf.runtime.task_doc import task_doc_contract_metadata

        self.task_store.patch_contract_fields(
            task_id,
            task_doc_contract_metadata(task_doc),
        )
        self.event_writer.append(ZfEvent(
            type="task.source.published",
            actor="orchestrator",
            task_id=task_id,
            payload={
                "dispatch_id": dispatch_id,
                "source_doc": str(task_doc.source_path),
                "source_revision": task_doc.source_revision,
            },
        ))
        self.event_writer.append(ZfEvent(
            type="task.doc.published",
            actor="orchestrator",
            task_id=task_id,
            payload={
                "dispatch_id": dispatch_id,
                "task_doc": str(task_doc.path),
                "manifest": str(task_doc.manifest_path),
                "source_revision": task_doc.source_revision,
                "contract_revision": task_doc.contract_revision,
                "capsule_revision": task_doc.capsule_revision,
            },
        ))

    def _recover_writer_fanout_task_bindings(self) -> None:
        """Re-project active writer fanout dispatches into canonical tasks."""
        from zf.runtime.workdirs import WorkdirManager

        try:
            events = self.event_log.read_all()
        except Exception:
            events = []
        self._cancel_orphan_active_fanout_manifests(events)
        events_by_id = {event.id: event for event in events if event.id}
        from zf.runtime.writer_fanout_generation import (
            completed_writer_generation,
        )

        fanout_root = self.state_dir / "fanouts"
        if not fanout_root.exists():
            return
        terminal_statuses = {"completed", "failed", "timed_out", "cancelled"}
        for manifest_path in fanout_root.glob("*/manifest.json"):
            fanout_id = manifest_path.parent.name
            manifest = self._fanout_manifest(fanout_id)
            if not manifest or manifest.get("topology") != "fanout_writer_scoped":
                continue
            aggregate = (
                manifest.get("aggregate")
                if isinstance(manifest.get("aggregate"), dict)
                else {}
            )
            if (
                str(manifest.get("status") or "") in terminal_statuses
                or str(aggregate.get("status") or "") in terminal_statuses
            ):
                continue
            trigger_event = events_by_id.get(
                str(manifest.get("trigger_event_id") or "")
            )
            if trigger_event is not None:
                trigger_payload = (
                    trigger_event.payload
                    if isinstance(trigger_event.payload, dict)
                    else {}
                )
                task_ids = {
                    str(child.get("task_id") or "").strip()
                    for child in manifest.get("children", []) or []
                    if isinstance(child, dict)
                    and str(child.get("task_id") or "").strip()
                }
                task_ids.update(
                    str(task_id).strip()
                    for task_id in trigger_payload.get("task_ids") or []
                    if str(task_id).strip()
                )
                completed_generation = completed_writer_generation(
                    events,
                    trigger_event=trigger_event,
                    task_ids=sorted(task_ids),
                    task_map_generation=str(
                        trigger_payload.get("task_map_generation") or ""
                    ),
                    workflow_run_id=str(
                        trigger_payload.get("workflow_run_id")
                        or trigger_payload.get("run_id")
                        or trigger_event.correlation_id
                        or ""
                    ),
                )
                if completed_generation is not None:
                    self._cancel_superseded_fanout_manifest(
                        fanout_id=fanout_id,
                        manifest=manifest,
                        reason="recovery_generation_already_verified",
                        superseded_by=(
                            completed_generation.candidate_event_id
                        ),
                        source="verified_writer_generation_recovery",
                    )
                    continue
            stale_reason, superseded_by = self._fanout_identity_stale_reason(fanout_id)
            if stale_reason:
                self._cancel_superseded_fanout_manifest(
                    fanout_id=fanout_id,
                    manifest=manifest,
                    reason=stale_reason,
                    superseded_by=superseded_by,
                    source="superseded_writer_fanout_manifest_closeout",
                )
                continue
            for child in manifest.get("children", []) or []:
                if not isinstance(child, dict):
                    continue
                if str(child.get("status") or "") != "dispatched":
                    continue
                task_id = str(child.get("task_id") or "")
                role_instance = str(child.get("role_instance") or "")
                run_id = str(child.get("run_id") or "")
                if not task_id or not role_instance or not run_id:
                    continue
                task = self.task_store.get(task_id)
                if task is None or task.status in {"done", "cancelled", "blocked"}:
                    continue
                if (
                    task.assigned_to == role_instance
                    and task.active_dispatch_id == run_id
                    and task.status == "in_progress"
                ):
                    self._publish_writer_fanout_task_capsule(
                        task_id=task_id,
                        dispatch_id=run_id,
                    )
                    continue
                if not self._claim_writer_fanout_task(
                    task_id,
                    role_instance,
                    run_id=run_id,
                ):
                    continue
                workdir_sync: dict[str, str] = {}
                roles = self._fanout_roles([role_instance])
                if roles:
                    manager = WorkdirManager(
                        state_dir=self.state_dir,
                        project_root=self.project_root,
                        config=self.config,
                    )
                    task_ref = manager.task_ref_metadata(task_id)
                    task_ref_trace_id = str(
                        task_ref.get("trace_id") or ""
                    ).strip()
                    source_ref = str(
                        task_ref.get("source_commit")
                        or task_ref.get("task_ref")
                        or ""
                    ).strip()
                    manifest_trace_id = str(
                        manifest.get("trace_id") or ""
                    ).strip()
                    if (
                        source_ref
                        and task_ref_trace_id in {"", manifest_trace_id}
                    ):
                        workdir_sync = manager.sync_writer_to_source_ref(
                            roles[0],
                            source_ref_override=source_ref,
                        )
                self._publish_writer_fanout_task_capsule(
                    task_id=task_id,
                    dispatch_id=run_id,
                )
                binding_payload = {
                    "dispatch_id": run_id,
                    "role_instance": role_instance,
                    "fanout_id": fanout_id,
                    "child_id": str(child.get("child_id") or ""),
                    "source": "writer_fanout_task_binding_recovery",
                }
                if workdir_sync:
                    binding_payload["workdir_sync"] = workdir_sync
                self.event_writer.append(ZfEvent(
                    type="task.dispatch_context.bound",
                    actor="zf-cli",
                    task_id=task_id,
                    payload=binding_payload,
                    causation_id=str(child.get("last_event_id") or "") or None,
                    correlation_id=str(manifest.get("trace_id") or "") or None,
                ))
                self._set_worker_state(
                    role_instance,
                    "busy",
                    reason="recovered writer fanout binding",
                    task_id=task_id,
                )

    def _cancel_superseded_fanout_manifest(
        self,
        *,
        fanout_id: str,
        manifest: dict,
        reason: str,
        superseded_by: str,
        source: str,
    ) -> None:
        if not fanout_id:
            return
        self._release_superseded_writer_fanout_dispatches(
            fanout_id=fanout_id,
            manifest=manifest,
            reason=reason,
            superseded_by=superseded_by,
        )
        try:
            events = self.event_log.read_all()
        except Exception:
            events = []
        cancelled_event = None
        for event in reversed(events):
            if event.type != "fanout.cancelled":
                continue
            payload = event.payload if isinstance(event.payload, dict) else {}
            if str(payload.get("fanout_id") or "") == fanout_id:
                cancelled_event = event
                break
        if cancelled_event is None:
            cancelled_event = self.event_writer.append(ZfEvent(
                type="fanout.cancelled",
                actor="zf-cli",
                payload={
                    "fanout_id": fanout_id,
                    "trace_id": str(manifest.get("trace_id") or ""),
                    "stage_id": str(manifest.get("stage_id") or ""),
                    "trigger_event_id": str(
                        manifest.get("trigger_event_id") or ""
                    ),
                    "target_ref": str(manifest.get("target_ref") or ""),
                    "pdd_id": str(manifest.get("pdd_id") or ""),
                    "feature_id": str(manifest.get("feature_id") or ""),
                    "task_map_ref": str(manifest.get("task_map_ref") or ""),
                    "reason": reason,
                    "superseded_by": superseded_by,
                    "source": source,
                },
                correlation_id=str(manifest.get("trace_id") or "") or None,
            ))
        self._cancel_superseded_reader_fanout_operations(
            manifest=manifest,
            reason=reason,
            cancellation_event=cancelled_event,
        )

    def _cancel_superseded_reader_fanout_operations(
        self,
        *,
        manifest: dict,
        reason: str,
        cancellation_event: ZfEvent,
    ) -> None:
        """Terminalize durable reader operations owned by a stale fanout."""

        if str(manifest.get("topology") or "") != "fanout_reader":
            return
        operation_ids: set[str] = set()
        operation_children: dict[str, dict] = {}
        for child in manifest.get("children", []) or []:
            if not isinstance(child, dict):
                continue
            child_payload = (
                child.get("payload")
                if isinstance(child.get("payload"), dict)
                else {}
            )
            operation_id = str(
                child.get("operation_id")
                or child_payload.get("operation_id")
                or ""
            ).strip()
            if operation_id:
                operation_ids.add(operation_id)
                operation_children[operation_id] = child
        if not operation_ids:
            return

        from zf.runtime.workflow_operation import (
            TERMINAL_OPERATION_STATUSES,
            WorkflowOperationService,
            reduce_workflow_operations,
        )

        operations = reduce_workflow_operations(self.event_log.read_all())
        service = WorkflowOperationService(
            state_dir=self.state_dir,
            event_log=self.event_log,
            event_writer=self.event_writer,
        )
        correlation_id = str(manifest.get("trace_id") or "")
        for operation_id in sorted(operation_ids):
            operation = operations.get(operation_id)
            if (
                operation is None
                or str(operation.get("status") or "")
                in TERMINAL_OPERATION_STATUSES
            ):
                continue
            request_hash = str(operation.get("request_hash") or "").strip()
            workflow_run_id = str(
                operation.get("workflow_run_id") or correlation_id
            ).strip()
            if not request_hash or not workflow_run_id:
                continue
            from zf.runtime.cancelled_run_workers import stop_cancelled_dispatch

            child = operation_children[operation_id]
            stop_cancelled_dispatch(
                self,
                role_instance=str(operation.get("role_instance")
                                  or child.get("role_instance") or ""),
                task_id=str(operation.get("task_id") or child.get("task_id") or ""),
                dispatch_id=str(operation.get("dispatch_id") or child.get("run_id") or ""),
                events=self.event_log.read_all(),
                cancellation=cancellation_event,
            )
            service.cancel(
                operation_id=operation_id,
                request_hash=request_hash,
                workflow_run_id=workflow_run_id,
                reason=f"superseded_reader_fanout: {reason}",
                task_id=str(operation.get("task_id") or ""),
                causation_id=cancellation_event.id,
                correlation_id=correlation_id or workflow_run_id,
            )

    def _cancel_orphan_active_fanout_manifests(
        self,
        events: list[ZfEvent],
    ) -> bool:
        """Fail-closed active fanout manifests that have no started event.

        ``events.jsonl`` is the runtime truth. A crash between manifest
        materialization and ``fanout.started`` append can leave a manifest at
        ``status=started`` forever. Recovery sweeps must not keep binding or
        redispatching those half-written projections.
        """
        fanout_root = self.state_dir / "fanouts"
        if not fanout_root.exists():
            return False
        terminal_statuses = {"completed", "failed", "timed_out", "cancelled"}
        started_ids: set[str] = set()
        terminal_ids: set[str] = set()
        for event in events:
            payload = event.payload if isinstance(event.payload, dict) else {}
            fanout_id = str(payload.get("fanout_id") or "")
            if not fanout_id:
                continue
            if event.type == "fanout.started":
                started_ids.add(fanout_id)
            elif event.type in {
                "fanout.cancelled",
                "fanout.timed_out",
                "fanout.aggregate.completed",
            }:
                terminal_ids.add(fanout_id)

        recovered = False
        for manifest_path in fanout_root.glob("*/manifest.json"):
            fanout_id = manifest_path.parent.name
            if fanout_id in started_ids or fanout_id in terminal_ids:
                continue
            manifest = self._fanout_manifest(fanout_id)
            if not manifest:
                continue
            topology = str(manifest.get("topology") or "")
            if topology not in {"fanout_writer_scoped", "fanout_reader"}:
                continue
            aggregate = (
                manifest.get("aggregate")
                if isinstance(manifest.get("aggregate"), dict)
                else {}
            )
            if (
                str(manifest.get("status") or "") in terminal_statuses
                or str(aggregate.get("status") or "") in terminal_statuses
            ):
                continue
            self.event_writer.append(ZfEvent(
                type="fanout.cancelled",
                actor="zf-cli",
                payload={
                    "fanout_id": fanout_id,
                    "trace_id": str(manifest.get("trace_id") or ""),
                    "stage_id": str(manifest.get("stage_id") or ""),
                    "trigger_event_id": str(manifest.get("trigger_event_id") or ""),
                    "target_ref": str(manifest.get("target_ref") or ""),
                    "pdd_id": str(manifest.get("pdd_id") or ""),
                    "feature_id": str(manifest.get("feature_id") or ""),
                    "task_map_ref": str(manifest.get("task_map_ref") or ""),
                    "reason": "fanout_manifest_without_started_event",
                    "source": "orphan_active_fanout_manifest_recovery",
                },
                correlation_id=str(manifest.get("trace_id") or "") or None,
            ))
            recovered = True
        return recovered

__all__ = ["FanoutRecoveryRuntimeMixin"]
