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

from zf.core.events.model import ZfEvent

RECOVERY_ACTIONS = (
    "task-requeue",
    "child-rebuild",
    "stage-retrigger",
    "rescan-grant",
)

_RETRIGGERABLE = frozenset({
    "task_map.ready", "lane.stage.completed", "flow.goal.closed",
    "flow.discovery.requested", "flow.discovery.completed",
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

    # -- 前置条件所需的账本视图(纯读) --------------------------------

    def _latest_child_state_for_task(self, task_id: str) -> tuple[str, str]:
        """返回 (state, child_id):inflight/completed/failed/none。"""
        state, child = "none", ""
        for event in self.writer.event_log.read_all():
            payload = event.payload if isinstance(event.payload, dict) else {}
            tid = str(payload.get("task_id") or event.task_id or "")
            if tid != task_id:
                continue
            if event.type == "fanout.child.dispatched":
                state, child = "inflight", str(payload.get("child_id") or "")
            elif event.type == "fanout.child.completed":
                state, child = "completed", str(payload.get("child_id") or "")
            elif event.type == "fanout.child.failed":
                state, child = "failed", str(payload.get("child_id") or "")
        return state, child

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
        child_state, child_id = self._latest_child_state_for_task(task_id)
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
        child_state, child_id = self._latest_child_state_for_task(task_id)
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
        emitted = self.writer.append(ZfEvent(
            type="task.rework.requested",
            actor=self.actor,
            task_id=task_id,
            payload={
                "task_id": task_id,
                "rework_of": child_id,
                "reason": str(
                    payload.get("reason")
                    or f"recovery: rebuild carrier for dead child {child_id}"
                ),
                "recovery_action": action,
            },
            causation_id=requested.id,
        ))
        return self._recovery_ok(requested, emitted, action, requested_action, {
            "task_id": task_id, "dead_child": child_id,
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
        if source.type not in _RETRIGGERABLE:
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
