"""恢复手术动词(RM agent 裁决闭环 Phase 2,2026-07-17)。

四个确定性动词,对应两轮 PRD E2E 里监工人肉做过 15+ 次的流程手术。
**裁决归 agent,执行归内核**:每个动词自带前置条件——条件不成立即
拒绝(agent 裁决错了也不伤真相);全部留痕、幂等、有界。

- task-requeue:  kanban WIP 与 fanout child 脱节时送回可派队列
  (前置:in_progress + 承接 child 已终局 + worker 非 busy)
- child-rebuild: 为死 child 走 rework 路由重建承接
  (前置:child 终局 + 任务未 done;代际由内核 rework 机器铸)
- stage-retrigger: 原样重发未消费/消费已败的推进事件,自动代际
  (前置:无同源 redrive;rework_of=源事件 id)
- rescan-grant:  goal idle 驱动器弹尽后追加一轮预算
  (前置:确有弹尽升级 + 距上次 grant 过冷却)
"""

from __future__ import annotations

import hashlib
import json

from zf.core.events.model import ZfEvent

RECOVERY_ACTIONS = (
    "task-requeue",
    "child-rebuild",
    "stage-retrigger",
    "stage-replan-new-generation",
    "fanout-aggregate-rebuild",
    "rescan-grant",
)

_RETRIGGERABLE = frozenset({
    "task_map.ready", "candidate.ready", "lane.stage.completed", "flow.goal.closed",
    "flow.discovery.requested", "flow.discovery.completed",
})
_UPSTREAM_LINEAGE_REBUILD_REASONS = frozenset({
    "blocking Goal closure has no current Plan Artifact Package",
    "current task-map generation has no pinned goal claim set",
})
_RESCAN_GRANT_COOLDOWN_S = 1800.0


class RecoveryActionsMixin:
    def _recovery_failed(
        self, requested, action, requested_action, reason, status_code=422,
    ) -> dict:
        return self._failed(
            requested=requested,
            action=action,
            requested_action=requested_action,
            task_id=None,
            reason=reason,
            status_code=status_code,
            status="failed",
        )

    def _recovery_ok(
        self, requested, event, action, requested_action, extra,
    ) -> dict:
        self._completed(
            requested=requested,
            event=event,
            action=action,
            requested_action=requested_action,
            status="applied",
            task_id=None,
            extra=extra,
        )
        return {
            "ok": True,
            "status": "applied",
            "action": action,
            "requested_action": requested_action,
            "event_id": event.id,
            **extra,
        }

    def _recovery_fanout_manifest(self, fanout_id: str) -> dict:
        try:
            manifest = json.loads(
                (
                    self.state_dir
                    / "fanouts"
                    / fanout_id
                    / "manifest.json"
                ).read_text(encoding="utf-8")
            )
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return {}
        return manifest if isinstance(manifest, dict) else {}

    # -- 前置条件所需的账本视图(纯读) --------------------------------

    def _latest_child_state_for_task(self, task_id: str) -> tuple[str, str, str]:
        """返回 (state, child_id, fanout_id)。"""
        state, child, fanout = "none", "", ""
        for event in self.writer.event_log.read_all():
            payload = event.payload if isinstance(event.payload, dict) else {}
            tid = str(payload.get("task_id") or event.task_id or "")
            if tid != task_id:
                continue
            if event.type == "fanout.child.dispatched":
                state, child = "inflight", str(payload.get("child_id") or "")
                fanout = str(payload.get("fanout_id") or "")
            elif event.type == "fanout.child.completed":
                state, child = "completed", str(payload.get("child_id") or "")
                fanout = str(payload.get("fanout_id") or "")
            elif event.type == "fanout.child.failed":
                state, child = "failed", str(payload.get("child_id") or "")
                fanout = str(payload.get("fanout_id") or "")
        return state, child, fanout

    def _task_status(self, task_id: str) -> tuple[str, str]:
        from zf.core.task.store import TaskStore

        task = TaskStore(self.state_dir / "kanban.json").get(task_id)
        if task is None:
            return "", ""
        return str(task.status or ""), str(task.assigned_to or "")

    # -- 动词 ---------------------------------------------------------

    def _task_requeue_action(
        self, *, requested: ZfEvent, action: str, requested_action: str, payload: dict,
    ) -> dict:
        task_id = str(payload.get("task_id") or "").strip()
        if not task_id:
            return self._recovery_failed(
                requested, action, requested_action, "task_id is required",
            )
        status, assignee = self._task_status(task_id)
        if status != "in_progress":
            return self._recovery_failed(
                requested, action, requested_action,
                f"precondition failed: task status is {status!r}, not in_progress",
                409,
            )
        child_state, child_id, _fanout_id = self._latest_child_state_for_task(task_id)
        if child_state == "inflight":
            return self._recovery_failed(
                requested, action, requested_action,
                f"precondition failed: live fanout child {child_id} carries this task",
                409,
            )
        emitted = self.writer.append(ZfEvent(
            type="task.requeued",
            actor=self.actor,
            task_id=task_id,
            payload={
                "task_id": task_id,
                "from_status": status,
                "to_status": "backlog",
                "assignee": assignee,
                "reason": str(payload.get("reason") or "recovery: wip_without_carrier"),
                "recovery_action": action,
            },
            causation_id=requested.id,
        ))
        return self._recovery_ok(requested, emitted, action, requested_action, {
            "task_id": task_id, "child_state": child_state,
        })

    def _child_rebuild_action(
        self, *, requested: ZfEvent, action: str, requested_action: str, payload: dict,
    ) -> dict:
        task_id = str(payload.get("task_id") or "").strip()
        if not task_id:
            return self._recovery_failed(
                requested, action, requested_action, "task_id is required",
            )
        status, _ = self._task_status(task_id)
        if status in ("done", ""):
            return self._recovery_failed(
                requested, action, requested_action,
                f"precondition failed: task status {status!r}",
                409,
            )
        child_state, child_id, fanout_id = self._latest_child_state_for_task(task_id)
        if child_state == "inflight":
            return self._recovery_failed(
                requested, action, requested_action,
                f"precondition failed: child {child_id} still in flight",
                409,
            )
        if child_state == "none":
            return self._recovery_failed(
                requested, action, requested_action,
                "precondition failed: no prior fanout child to rebuild from",
                409,
            )
        action_id = f"child-rebuild-{requested.id}"
        emitted = self.writer.append(ZfEvent(
            type="repair.action.requested",
            actor=self.actor,
            task_id=task_id,
            payload={
                "action_id": action_id,
                "kind": "rerun_fanout_child",
                "idempotency_key": (
                    f"child-rebuild:{fanout_id}:{child_id}:{requested.id}"
                ),
                "task_id": task_id,
                "fanout_id": fanout_id,
                "fanout_child_id": child_id,
                "reason": str(
                    payload.get("reason")
                    or f"recovery: rebuild carrier for dead child {child_id}"
                ),
                "recovery_action": action,
            },
            causation_id=requested.id,
        ))
        return self._recovery_ok(requested, emitted, action, requested_action, {
            "task_id": task_id,
            "dead_child": child_id,
            "fanout_id": fanout_id,
            "repair_action_id": action_id,
        })

    def _stage_retrigger_action(
        self, *, requested: ZfEvent, action: str, requested_action: str, payload: dict,
    ) -> dict:
        source_event_id = str(payload.get("source_event_id") or "").strip()
        if not source_event_id:
            return self._recovery_failed(
                requested, action, requested_action, "source_event_id is required",
            )
        source = None
        for event in self.writer.event_log.read_all():
            if event.id == source_event_id:
                source = event
                break
        if source is None:
            return self._recovery_failed(
                requested, action, requested_action,
                f"source event {source_event_id} not found", 404,
            )
        registered_reader_trigger = any(
            stage.topology == "fanout_reader" and stage.trigger == source.type
            for stage in getattr(getattr(self.config, "workflow", None), "stages", [])
        )
        if source.type not in _RETRIGGERABLE and not registered_reader_trigger:
            return self._recovery_failed(
                requested, action, requested_action,
                f"{source.type} is not a retriggerable driving event",
            )
        for event in self.writer.event_log.read_all():
            body = event.payload if isinstance(event.payload, dict) else {}
            if str(body.get("redrive_of") or "") == source_event_id:
                return self._recovery_failed(
                    requested, action, requested_action,
                    f"already retriggered by {event.id}", 409,
                )
        base = dict(source.payload if isinstance(source.payload, dict) else {})
        base["redrive_of"] = source_event_id
        base["rework_of"] = source_event_id  # 代际:retrigger 不是 replay
        emitted = self.writer.append(ZfEvent(
            type=source.type,
            actor=self.actor,
            task_id=source.task_id,
            payload=base,
            causation_id=requested.id,
            correlation_id=source.correlation_id,
        ))
        return self._recovery_ok(requested, emitted, action, requested_action, {
            "retriggered_event_id": emitted.id,
            "source_event_id": source_event_id,
            "event_type": source.type,
        })

    def _stage_replan_new_generation_action(
        self, *, requested: ZfEvent, action: str, requested_action: str, payload: dict,
    ) -> dict:
        escalation_event_id = str(
            payload.get("escalation_event_id") or ""
        ).strip()
        approval_ref = str(payload.get("approval_ref") or "").strip()
        if not escalation_event_id or not approval_ref:
            return self._recovery_failed(
                requested,
                action,
                requested_action,
                "escalation_event_id and approval_ref are required",
            )
        events = self.writer.event_log.read_all()
        escalation = next(
            (event for event in events if event.id == escalation_event_id),
            None,
        )
        escalation_payload = (
            escalation.payload
            if escalation is not None and isinstance(escalation.payload, dict)
            else {}
        )
        if escalation is None or escalation.type != "human.escalate":
            return self._recovery_failed(
                requested,
                action,
                requested_action,
                f"human escalation {escalation_event_id!r} was not found",
                404,
            )
        if str(escalation_payload.get("failure_class") or "") != (
            "stage_replan_cap_exhausted"
        ):
            return self._recovery_failed(
                requested,
                action,
                requested_action,
                "escalation is not a stage_replan_cap_exhausted failure",
                409,
            )
        if "start_new_generation" not in {
            str(item) for item in escalation_payload.get("allowed_actions") or []
        }:
            return self._recovery_failed(
                requested,
                action,
                requested_action,
                "escalation does not authorize start_new_generation",
                409,
            )
        source_event_id = str(
            escalation_payload.get("source_event_id")
            or escalation.causation_id
            or ""
        ).strip()
        source = next(
            (event for event in events if event.id == source_event_id),
            None,
        )
        if source is None:
            return self._recovery_failed(
                requested,
                action,
                requested_action,
                f"stage failure {source_event_id!r} was not found",
                404,
            )
        from zf.runtime.run_scope import resolve_run_for_event

        run_id = resolve_run_for_event(events, escalation) or (
            resolve_run_for_event(events, source)
        )
        if not run_id:
            return self._recovery_failed(
                requested,
                action,
                requested_action,
                "stage failure has no canonical workflow Run",
                409,
            )
        generation = "stage-replan-" + hashlib.sha256(
            f"{run_id}|{escalation.id}".encode("utf-8")
        ).hexdigest()[:20]
        existing_trigger = next(
            (
                event for event in events
                if str((event.payload or {}).get("stage_replan_generation") or "")
                == generation
                and event.type not in {
                    "run.goal.updated",
                    "task.status_changed",
                }
            ),
            None,
        )
        if existing_trigger is not None:
            if payload.get("repair_target") != existing_trigger.payload.get("reader_repair_target"):
                return self._recovery_failed(
                    requested, action, requested_action,
                    "stage generation already exists with a different repair target", 409,
                )
            return self._recovery_ok(
                requested,
                existing_trigger,
                action,
                requested_action,
                {
                    "replayed": True,
                    "run_id": run_id,
                    "stage_replan_generation": generation,
                    "stage_trigger_event_id": existing_trigger.id,
                },
            )
        from zf.runtime.stage_failure_replan import plan_reader_stage_replan

        stage_trigger, note = plan_reader_stage_replan(
            self.config,
            events,
            source,
            authorized_generation=generation,
            generation_source_event_id=escalation.id,
            authorization_event_id=requested.id,
            authorization_ref=approval_ref,
        )
        if stage_trigger is None:
            return self._recovery_failed(
                requested,
                action,
                requested_action,
                f"new stage generation rejected: {note}",
                409,
            )
        if "repair_target" in payload:
            from zf.runtime.reader_repair_target import bind_reader_repair_target

            try:
                bind_reader_repair_target(
                    project_root=self.project_root, state_dir=self.state_dir,
                    trigger=stage_trigger, repair=payload["repair_target"],
                    source_event_id=requested.id,
                )
            except ValueError as exc:
                return self._recovery_failed(
                    requested, action, requested_action, str(exc), 409,
                )
        goal_update = next(
            (
                event for event in events
                if event.type == "run.goal.updated"
                and str((event.payload or {}).get("stage_replan_generation") or "")
                == generation
            ),
            None,
        )
        task_id = str(
            payload.get("task_id")
            or escalation.task_id
            or source.task_id
            or ""
        ).strip()
        if goal_update is None:
            goal_update = self.writer.append(ZfEvent(
                type="run.goal.updated",
                actor=self.actor,
                task_id=task_id or None,
                causation_id=requested.id,
                correlation_id=run_id,
                payload={
                    "schema_version": "run-goal.update.v1",
                    "run_id": run_id,
                    "workflow_run_id": run_id,
                    "status": "active",
                    "reason": "authorized_stage_replan_new_generation",
                    "stage_replan_generation": generation,
                    "stage_replan_generation_source_event_id": escalation.id,
                    "source_failure_event_id": source.id,
                    "approval_ref": approval_ref,
                },
            ))
        from zf.core.task.store import TaskStore
        from zf.runtime.workflow_task_lifecycle import (
            reopen_workflow_managed_task_from_run_recovery,
        )

        reopen_workflow_managed_task_from_run_recovery(
            task_store=TaskStore(self.state_dir / "kanban.json"),
            event_writer=self.writer,
            recovery_event=goal_update,
        )
        emitted = self.writer.append(stage_trigger)
        return self._recovery_ok(requested, emitted, action, requested_action, {
            "replayed": False,
            "run_id": run_id,
            "stage_replan_generation": generation,
            "run_goal_update_event_id": goal_update.id,
            "stage_trigger_event_id": emitted.id,
            "source_failure_event_id": source.id,
            "escalation_event_id": escalation.id,
        })

    def _fanout_aggregate_rebuild_action(
        self, *, requested: ZfEvent, action: str, requested_action: str, payload: dict,
    ) -> dict:
        source_event_id = str(payload.get("source_event_id") or "").strip()
        if not source_event_id:
            return self._recovery_failed(
                requested, action, requested_action, "source_event_id is required",
            )
        events = self.writer.event_log.read_all()
        source = next(
            (event for event in events if event.id == source_event_id),
            None,
        )
        if source is None:
            return self._recovery_failed(
                requested, action, requested_action,
                f"source event {source_event_id} not found", 404,
            )
        invalid_event = None
        schema_failure_event = None
        aggregate_event = source
        if source.type == "goal.closure.identity.invalid":
            invalid_event = source
            invalid_payload = (
                source.payload if isinstance(source.payload, dict) else {}
            )
            aggregate_event_id = str(
                invalid_payload.get("source_event_id") or ""
            ).strip()
            aggregate_event = next(
                (event for event in events if event.id == aggregate_event_id),
                None,
            )
        elif source.type == "discriminator.failed":
            source_payload = (
                source.payload if isinstance(source.payload, dict) else {}
            )
            blocked_event_type = str(
                source_payload.get("blocked_event_type") or ""
            ).strip()
            blocked_payload = source_payload.get("blocked_event_payload")
            if (
                blocked_event_type == "goal.closure.synthesized"
                and isinstance(blocked_payload, dict)
            ):
                schema_failure_event = source
                aggregate_event = source
        if (
            aggregate_event is None
            or (
                aggregate_event.type != "flow.discovery.completed"
                and schema_failure_event is None
            )
        ):
            return self._recovery_failed(
                requested,
                action,
                requested_action,
                "aggregate rebuild only accepts flow.discovery.completed, "
                "its goal.closure.identity.invalid event, or a discriminator "
                "that blocked goal.closure.synthesized",
                409,
            )
        if schema_failure_event is not None:
            schema_failure_payload = (
                schema_failure_event.payload
                if isinstance(schema_failure_event.payload, dict)
                else {}
            )
            aggregate_payload = (
                schema_failure_payload.get("blocked_event_payload")
                if isinstance(
                    schema_failure_payload.get("blocked_event_payload"),
                    dict,
                )
                else {}
            )
        else:
            aggregate_payload = (
                aggregate_event.payload
                if isinstance(aggregate_event.payload, dict)
                else {}
            )
        fanout_id = str(aggregate_payload.get("fanout_id") or "").strip()
        if not fanout_id:
            return self._recovery_failed(
                requested,
                action,
                requested_action,
                "source aggregate has no fanout_id",
                409,
            )
        manifest = self._recovery_fanout_manifest(fanout_id)
        if not manifest:
            return self._recovery_failed(
                requested,
                action,
                requested_action,
                f"fanout manifest {fanout_id!r} is missing",
                404,
            )
        rebuild_scope = (
            "blocked_success_aggregate"
            if schema_failure_event is not None
            else "source_aggregate"
        )
        invalid_payload = (
            invalid_event.payload
            if invalid_event is not None
            and isinstance(invalid_event.payload, dict)
            else {}
        )
        if str(invalid_payload.get("reason") or "") in _UPSTREAM_LINEAGE_REBUILD_REASONS:
            trigger_payload = (
                manifest.get("trigger_payload")
                if isinstance(manifest.get("trigger_payload"), dict)
                else {}
            )
            upstream_event_id = str(
                trigger_payload.get("source_event_id") or ""
            ).strip()
            upstream_event = next(
                (event for event in events if event.id == upstream_event_id),
                None,
            )
            upstream_payload = (
                upstream_event.payload
                if upstream_event is not None
                and isinstance(upstream_event.payload, dict)
                else {}
            )
            upstream_fanout_id = str(
                upstream_payload.get("fanout_id") or ""
            ).strip()
            upstream_manifest = self._recovery_fanout_manifest(
                upstream_fanout_id
            )
            upstream_aggregate_config = (
                upstream_manifest.get("aggregate_config")
                if isinstance(upstream_manifest.get("aggregate_config"), dict)
                else {}
            )
            if (
                upstream_event is None
                or not upstream_fanout_id
                or not upstream_manifest
                or str(upstream_aggregate_config.get("success_event") or "")
                != upstream_event.type
            ):
                return self._recovery_failed(
                    requested,
                    action,
                    requested_action,
                    "lineage recovery requires a durable upstream reader aggregate",
                    409,
                )
            aggregate_event = upstream_event
            aggregate_payload = upstream_payload
            fanout_id = upstream_fanout_id
            manifest = upstream_manifest
            rebuild_scope = "upstream_lineage_aggregate"
        aggregate_config = (
            manifest.get("aggregate_config")
            if isinstance(manifest.get("aggregate_config"), dict)
            else {}
        )
        expected_success_event = str(
            aggregate_config.get("success_event") or ""
        )
        observed_success_event = (
            str(
                (
                    schema_failure_event.payload
                    if schema_failure_event is not None
                    and isinstance(schema_failure_event.payload, dict)
                    else {}
                ).get("blocked_event_type")
                or ""
            )
            if schema_failure_event is not None
            else aggregate_event.type
        )
        if aggregate_config and expected_success_event != observed_success_event:
            return self._recovery_failed(
                requested,
                action,
                requested_action,
                "source event does not match fanout aggregate success event",
                409,
            )
        try:
            from zf.runtime.fanout_identity import fanout_current_status

            current = fanout_current_status(events, fanout_id)
        except Exception:
            current = None
        if current is not None and not current.current:
            return self._recovery_failed(
                requested,
                action,
                requested_action,
                (
                    f"fanout {fanout_id!r} is stale"
                    + (
                        f": {current.stale_reason}"
                        if current.stale_reason
                        else ""
                    )
                ),
                409,
            )
        children = [
            child
            for child in manifest.get("children", []) or []
            if isinstance(child, dict)
        ]
        if not children or not all(
            str(child.get("status") or "") in {"completed", "failed"}
            for child in children
        ):
            return self._recovery_failed(
                requested,
                action,
                requested_action,
                "fanout children are not terminal",
                409,
            )
        for event in events:
            event_payload = event.payload if isinstance(event.payload, dict) else {}
            if (
                event.type == "fanout.aggregate.rebuild.requested"
                and str(event_payload.get("source_event_id") or "")
                == aggregate_event.id
            ):
                return self._recovery_failed(
                    requested,
                    action,
                    requested_action,
                    f"aggregate already has rebuild request {event.id}",
                    409,
                )
        emitted = self.writer.append(ZfEvent(
            type="fanout.aggregate.rebuild.requested",
            actor=self.actor,
            payload={
                "schema_version": "fanout.aggregate-rebuild.v1",
                "fanout_id": fanout_id,
                "source_event_id": aggregate_event.id,
                "identity_invalid_event_id": (
                    invalid_event.id if invalid_event is not None else ""
                ),
                "schema_failure_event_id": (
                    schema_failure_event.id
                    if schema_failure_event is not None
                    else ""
                ),
                "rebuild_scope": rebuild_scope,
                "expected_success_event": expected_success_event,
                "reason": str(
                    payload.get("reason")
                    or "rebuild aggregate from durable manifest with current reducer"
                ),
            },
            causation_id=requested.id,
            correlation_id=aggregate_event.correlation_id,
        ))
        return self._recovery_ok(requested, emitted, action, requested_action, {
            "fanout_id": fanout_id,
            "source_event_id": aggregate_event.id,
            "rebuild_scope": rebuild_scope,
            "rebuild_request_event_id": emitted.id,
        })

    def _rescan_grant_action(
        self, *, requested: ZfEvent, action: str, requested_action: str, payload: dict,
    ) -> dict:
        exhausted = False
        last_grant = 0.0
        from datetime import datetime

        for event in self.writer.event_log.read_all():
            if event.type == "human.escalate":
                body = event.payload if isinstance(event.payload, dict) else {}
                if "rescan" in str(body.get("reason") or ""):
                    exhausted = True
            elif event.type == "run.goal.rescan.granted":
                try:
                    last_grant = max(
                        last_grant,
                        datetime.fromisoformat(str(event.ts)).timestamp(),
                    )
                except (ValueError, TypeError):
                    pass
        if not exhausted:
            return self._recovery_failed(
                requested, action, requested_action,
                "precondition failed: no rescan-exhausted escalation on record",
                409,
            )
        import time as _time

        if last_grant and _time.time() - last_grant < _RESCAN_GRANT_COOLDOWN_S:
            return self._recovery_failed(
                requested, action, requested_action,
                "precondition failed: last grant within cooldown", 429,
            )
        emitted = self.writer.append(ZfEvent(
            type="run.goal.rescan.granted",
            actor=self.actor,
            payload={
                "reason": str(payload.get("reason") or "recovery: grant one more idle rescan round"),
                "recovery_action": action,
            },
            causation_id=requested.id,
        ))
        return self._recovery_ok(requested, emitted, action, requested_action, {
            "granted_event_id": emitted.id,
        })


__all__ = ["RECOVERY_ACTIONS", "RecoveryActionsMixin"]
