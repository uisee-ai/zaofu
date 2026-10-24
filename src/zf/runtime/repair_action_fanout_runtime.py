"""Controlled fanout-child rerun execution for repair actions."""

from __future__ import annotations

from zf.core.events.model import ZfEvent
from zf.runtime.workflow_runtime_types import WorkflowRuntimeDecision


class RepairActionFanoutRuntimeMixin:
    """Host method that validates and reruns one failed fanout child."""

    def _rerun_fanout_child(
        self,
        fanout_id: str,
        child_id: str,
    ) -> WorkflowRuntimeDecision:
        manifest = self._fanout_manifest(fanout_id)
        if not manifest:
            return WorkflowRuntimeDecision(
                action="rerun_fanout_child_failed",
                reason=f"unknown_fanout:{fanout_id}",
            )
        child = self._fanout_child(manifest, child_id)
        if child is None:
            return WorkflowRuntimeDecision(
                action="rerun_fanout_child_failed",
                reason=f"unknown_fanout_child:{child_id}",
            )
        role_instance = str(child.get("role_instance") or "")
        task_id = str(child.get("task_id") or "")
        status = str(child.get("status") or "")
        if status == "completed":
            return WorkflowRuntimeDecision(
                action="rerun_fanout_child_failed",
                role=role_instance,
                task_id=task_id,
                reason=f"fanout_child_terminal:{status}",
            )
        stale_reason, superseded_by = self._fanout_identity_stale_reason(fanout_id)
        if stale_reason:
            suffix = f":{superseded_by}" if superseded_by else ""
            return WorkflowRuntimeDecision(
                action="rerun_fanout_child_failed",
                role=role_instance,
                task_id=task_id,
                reason=f"stale_fanout:{stale_reason}{suffix}",
            )
        role = next(iter(self._fanout_roles([role_instance])), None)
        if role is None:
            return WorkflowRuntimeDecision(
                action="rerun_fanout_child_failed",
                role=role_instance,
                task_id=task_id,
                reason=f"unknown_worker:{role_instance or '(missing)'}",
            )
        events = self.event_log.read_all()
        dispatches: list[tuple[int, ZfEvent]] = []
        terminal_events: list[tuple[int, ZfEvent]] = []
        for event_index, event in enumerate(events):
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
                dispatches.append((event_index, event))
            else:
                terminal_events.append((event_index, event))
        if terminal_events and terminal_events[-1][1].type == "fanout.child.completed":
            return WorkflowRuntimeDecision(
                action="rerun_fanout_child_failed",
                role=role.instance_id,
                task_id=task_id,
                reason="fanout_child_terminal:fanout.child.completed",
            )
        if not dispatches:
            return WorkflowRuntimeDecision(
                action="rerun_fanout_child_failed",
                role=role.instance_id,
                task_id=task_id,
                reason="missing_previous_fanout_child_dispatch",
            )
        interrupted_dispatch: ZfEvent | None = None
        latest_dispatch_is_open = (
            not terminal_events
            or dispatches[-1][0] > terminal_events[-1][0]
        )
        if latest_dispatch_is_open:
            latest_payload = (
                dispatches[-1][1].payload
                if isinstance(dispatches[-1][1].payload, dict)
                else {}
            )
            operation_id = str(latest_payload.get("operation_id") or "")
            from zf.runtime.workflow_operation import reduce_workflow_operations

            operation = reduce_workflow_operations(events).get(operation_id, {})
            operation_status = str(operation.get("status") or "")
            if terminal_events and operation_status not in {"suspended", "cancelled", "blocked"}:
                return WorkflowRuntimeDecision(
                    action="rerun_fanout_child_failed",
                    role=role.instance_id,
                    task_id=task_id,
                    reason="fanout_child_has_newer_dispatch",
                )
            if operation_status in {"suspended", "cancelled", "blocked"}:
                interrupted_dispatch = dispatches[-1][1]
        previous_dispatch = dispatches[-1][1]
        if interrupted_dispatch is not None:
            interrupted_payload = (
                interrupted_dispatch.payload
                if isinstance(interrupted_dispatch.payload, dict)
                else {}
            )
            interrupted_run_id = str(interrupted_payload.get("run_id") or "")
            self.event_writer.append(ZfEvent(
                type="fanout.child.dispatch_lost",
                actor="zf-cli",
                task_id=task_id or None,
                payload={
                    "fanout_id": fanout_id,
                    "trace_id": str(manifest.get("trace_id") or ""),
                    "stage_id": str(manifest.get("stage_id") or ""),
                    "child_id": child_id,
                    "run_id": interrupted_run_id,
                    "role_instance": role.instance_id,
                    "task_id": task_id,
                    "operation_id": str(
                        interrupted_payload.get("operation_id") or ""
                    ),
                    "reason": f"{operation_status}_operation_replaced_by_controlled_rerun",
                    "source": "repair_action_executor",
                },
                causation_id=interrupted_dispatch.id,
                correlation_id=str(manifest.get("trace_id") or "") or None,
            ))
            task = self.task_store.get(task_id)
            if (
                task is not None
                and str(task.active_dispatch_id or "") == interrupted_run_id
            ):
                self.task_store.update(
                    task_id,
                    assigned_to="",
                    active_dispatch_id="",
                )
            if (
                getattr(self, "_active_dispatch_ids", {}).get(task_id)
                == interrupted_run_id
            ):
                self._active_dispatch_ids.pop(task_id, None)
            self._set_worker_state(
                role.instance_id,
                "idle",
                reason=f"{operation_status} fanout dispatch released for controlled rerun",
                task_id=task_id,
                force=True,
            )
        dispatched = self._retry_fanout_child(
            manifest=manifest,
            child=child,
            previous_dispatch=previous_dispatch,
            attempt=len(dispatches),
            fresh_operation=True,
        )
        if not dispatched:
            return WorkflowRuntimeDecision(
                action="rerun_fanout_child_failed",
                role=role.instance_id,
                task_id=task_id,
                reason="fanout child rerun deferred before transport dispatch",
            )
        return WorkflowRuntimeDecision(
            action="rerun_fanout_child",
            role=role.instance_id,
            task_id=task_id,
            reason="fanout child rerun dispatched",
        )


__all__ = ["RepairActionFanoutRuntimeMixin"]
