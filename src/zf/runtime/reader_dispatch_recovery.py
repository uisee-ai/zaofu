"""Bounded recovery of interrupted reader fanout dispatches."""

from __future__ import annotations

from zf.core.events.model import ZfEvent
from zf.runtime.run_admission import build_run_admission_projection


class ReaderDispatchRecoveryMixin:
    def _recover_lost_reader_fanout_dispatches(
        self,
        events: list[ZfEvent],
        *,
        recovery_snapshot=None,
    ) -> bool:
        """Retry reader children whose dispatched provider session was replaced.

        Reader fanout children often have no kanban task id, so a watcher
        restart or context refresh can leave the manifest at ``dispatched``
        while the worker pane/session has been refreshed back to idle. Treat
        explicit session replacement events or a provider turn that closes
        without a canonical child result as infrastructure loss. Rerun within
        the bounded recovery allowance, then fail explicitly instead of
        leaving the child dispatched until its wall-clock budget expires.
        """
        terminal_statuses = {"completed", "failed", "timed_out", "cancelled"}
        event_index = {event.id: index for index, event in enumerate(events)}
        dispatches: dict[tuple[str, str], list[ZfEvent]] = {}
        terminal_children: set[tuple[str, str]] = set()
        lost_events_by_role: dict[str, list[tuple[int, ZfEvent]]] = {}
        dispatch_lost_count: dict[tuple[str, str], int] = {}
        pending_loss: dict[tuple[str, str], ZfEvent] = {}
        from zf.runtime.workflow_operation import reduce_workflow_operations

        admission = build_run_admission_projection(events)
        operations = reduce_workflow_operations(events)
        for index, event in enumerate(events):
            payload = event.payload if isinstance(event.payload, dict) else {}
            if event.type == "fanout.child.dispatched":
                fanout_id = str(payload.get("fanout_id") or "")
                child_id = str(payload.get("child_id") or "")
                if fanout_id and child_id:
                    key = (fanout_id, child_id)
                    dispatches.setdefault(key, []).append(event)
                    if pending_loss.pop(key, None) is not None:
                        dispatch_lost_count[key] = dispatch_lost_count.get(key, 0) + 1
                continue
            if event.type in {"fanout.child.completed", "fanout.child.failed"}:
                fanout_id = str(payload.get("fanout_id") or "")
                child_id = str(payload.get("child_id") or "")
                if fanout_id and child_id:
                    terminal_children.add((fanout_id, child_id))
                continue
            if event.type == "fanout.child.dispatch_lost":
                fanout_id = str(payload.get("fanout_id") or "")
                child_id = str(payload.get("child_id") or "")
                if fanout_id and child_id:
                    key = (fanout_id, child_id)
                    pending_loss[key] = event
                continue
            role_instance = self._reader_dispatch_lost_role(event)
            if role_instance:
                lost_events_by_role.setdefault(role_instance, []).append((index, event))

        if recovery_snapshot is not None:
            manifests = recovery_snapshot.manifests
        else:
            fanout_root = self.state_dir / "fanouts"
            if not fanout_root.exists():
                return False
            manifests = (
                (path.parent.name, self._fanout_manifest(path.parent.name))
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
            if recovery_snapshot is None:
                stale_reason, _superseded_by = (
                    self._fanout_identity_stale_reason(fanout_id)
                )
            else:
                stale_reason, _superseded_by = (
                    self._fanout_identity_stale_reason(
                        fanout_id,
                        currentness=recovery_snapshot.currentness,
                    )
                )
            if stale_reason:
                continue
            max_retries = int(
                (manifest.get("aggregate_config") or {}).get("max_retries") or 0
            )
            allowed_recoveries = max(1, max_retries)
            for child in manifest.get("children", []) or []:
                if not isinstance(child, dict):
                    continue
                if str(child.get("status") or "") != "dispatched":
                    continue
                child_id = str(child.get("child_id") or "")
                role_instance = str(child.get("role_instance") or "")
                if not child_id or not role_instance:
                    continue
                key = (fanout_id, child_id)
                if key in terminal_children:
                    continue
                child_dispatches = dispatches.get(key, [])
                if not child_dispatches:
                    continue
                latest_dispatch = child_dispatches[-1]
                from zf.runtime.reader_recovery_owner import another_reader_owns_role

                if another_reader_owns_role(events, latest_dispatch):
                    continue
                workflow_run_id = str(
                    latest_dispatch.payload.get("workflow_run_id")
                    or manifest.get("trace_id") or ""
                )
                run = admission.runs.get(workflow_run_id)
                if run is not None and run.status != "running":
                    continue
                latest_index = event_index.get(latest_dispatch.id, -1)
                if latest_index < 0:
                    continue
                if self._fanout_dispatch_has_authoritative_result(
                    events,
                    latest_dispatch,
                ):
                    continue
                from zf.runtime.synth_budget_recovery import pending_reader_budget_amendment

                operation = operations.get(str(latest_dispatch.payload.get("operation_id") or ""), {})
                amendment = pending_reader_budget_amendment(events, operation)
                if amendment is not None:
                    dispatch_payload = latest_dispatch.payload
                    nested = dispatch_payload.get("payload")
                    nested = nested if isinstance(nested, dict) else {}
                    if operation.get("status") not in {"requested", "suspended"} or any((
                        str(operation.get("workflow_run_id") or "") != workflow_run_id,
                        str(operation.get("role_instance") or "") != role_instance,
                        str(dispatch_payload.get("role_instance") or "") != role_instance,
                        str(operation.get("request_hash") or "") != str(
                            dispatch_payload.get("request_hash") or nested.get("request_hash") or ""
                        ),
                        str(operation.get("active_attempt_id") or "") != str(
                            nested.get("attempt_id") or dispatch_payload.get("attempt_id") or ""
                        ),
                    )):
                        continue
                    self._set_worker_state(
                        role_instance, "idle", reason="owner_budget_amended", force=True
                    )
                    if self._retry_fanout_child(
                        manifest=manifest, child=child, previous_dispatch=latest_dispatch,
                        attempt=len(child_dispatches), provider_session_replaced=True,
                        recovery_decision_event=amendment,
                    ):
                        return True
                    continue
                lost_event = self._reader_dispatch_lost_event_after(
                    lost_events_by_role.get(role_instance, []),
                    latest_index,
                    events=events, dispatch_event=latest_dispatch,
                    state_dir=self.state_dir,
                )
                if lost_event is None:
                    continue
                lost_index = event_index.get(lost_event.id, latest_index)
                activity_index = (
                    latest_index
                    if lost_event.type == "cost.usage.capture_miss"
                    else lost_index
                )
                if self._reader_role_has_activity_after_dispatch(
                    events,
                    role_instance,
                    activity_index,
                ):
                    continue
                provider_turn_closed = lost_event.type == "provider.turn.closed"
                loss_reason = (
                    "provider_turn_closed_without_child_result"
                    if provider_turn_closed
                    else "reader_worker_session_replaced_after_dispatch"
                )
                dispatch_lost_event = pending_loss.get(key)
                if (
                    dispatch_lost_event is None
                    or dispatch_lost_event.payload.get("lost_signal_event_id") != lost_event.id
                ):
                    dispatch_lost_event = self.event_writer.append(ZfEvent(
                        type="fanout.child.dispatch_lost",
                        actor="zf-cli",
                        task_id=str(child.get("task_id") or "") or None,
                        payload={
                            "fanout_id": fanout_id,
                            "trace_id": str(manifest.get("trace_id") or ""),
                            "stage_id": str(manifest.get("stage_id") or ""),
                            "child_id": child_id,
                            "run_id": str(child.get("run_id") or ""),
                            "role_instance": role_instance,
                            "task_id": str(child.get("task_id") or ""),
                            "reason": loss_reason,
                            "lost_signal_event_id": lost_event.id,
                            "lost_signal_type": lost_event.type,
                        },
                        causation_id=lost_event.id,
                        correlation_id=str(manifest.get("trace_id") or ""),
                    ))
                self._set_worker_state(
                    role_instance,
                    "idle",
                    reason=loss_reason,
                    force=True,
                )
                if dispatch_lost_count.get(key, 0) >= allowed_recoveries:
                    self._fail_exhausted_reader_fanout_dispatch(
                        manifest=manifest,
                        child=child,
                        lost_event=lost_event,
                        loss_reason=loss_reason,
                    )
                    return True
                self._retry_fanout_child(
                    manifest=manifest,
                    child=child,
                    previous_dispatch=latest_dispatch,
                    attempt=len(child_dispatches),
                    provider_session_replaced=True,
                    recovery_decision_event=dispatch_lost_event,
                )
                return True
        return False
