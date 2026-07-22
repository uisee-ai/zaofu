"""Recovery helpers shared by reader and writer fanout coordination."""

from __future__ import annotations

from zf.core.events.model import ZfEvent


class FanoutRecoveryRuntimeMixin:
    def _recover_writer_fanout_task_bindings(self) -> None:
        """Re-project active writer fanout dispatches into canonical tasks."""
        try:
            events = self.event_log.read_all()
        except Exception:
            events = []
        self._cancel_orphan_active_fanout_manifests(events)
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
                    continue
                if not self._claim_writer_fanout_task(
                    task_id,
                    role_instance,
                    run_id=run_id,
                ):
                    continue
                self.event_writer.append(ZfEvent(
                    type="task.dispatch_context.bound",
                    actor="zf-cli",
                    task_id=task_id,
                    payload={
                        "dispatch_id": run_id,
                        "role_instance": role_instance,
                        "fanout_id": fanout_id,
                        "child_id": str(child.get("child_id") or ""),
                        "source": "writer_fanout_task_binding_recovery",
                    },
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
        for event in reversed(events):
            if event.type != "fanout.cancelled":
                continue
            payload = event.payload if isinstance(event.payload, dict) else {}
            if str(payload.get("fanout_id") or "") == fanout_id:
                return
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
                "reason": reason,
                "superseded_by": superseded_by,
                "source": source,
            },
            correlation_id=str(manifest.get("trace_id") or "") or None,
        ))

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
