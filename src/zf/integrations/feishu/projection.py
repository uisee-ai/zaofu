"""Feishu projection router — events → views → channel routing."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from zf.core.events.model import ZfEvent
from zf.core.task.store import TaskStore
from zf.integrations.feishu.views import SummaryView, TaskView
from zf.integrations.feishu.transport import FeishuTransport, FeishuMessage
from zf.runtime.event_problem_registry import spec_for_event


# Event types that trigger push notifications
_MUST_PUSH = {
    "human.escalate", "cost.exceeded", "review.rejected",
    "test.failed", "task.done", "loop.completed",
    # G-LIFE-4: stuck detection also needs to reach humans via Feishu
    "worker.stuck",
    # G-RECYCLE-7: context-window recycle lifecycle
    "worker.context.warning",
    "worker.context.critical",
    "worker.recycling",
    "worker.recycled",
    "worker.recycle.failed",
    # G-WIRE-1/2/3: scope + drift + refresh observation
    "scope.violation",
    "worker.drift.detected",
    "worker.refresh.triggered",
    # G-COST-BLOCK-1: hard budget block
    "cost.budget.exceeded",
    # G-DISC-4: discriminator AND closure
    "discriminator.failed",
    "discriminator.passed",
    # rework / failure / retry lifecycle + autoresearch bug — operator wants
    # these on Feishu (AI-native report 场景 1/3). Low-frequency milestones.
    "integration.failed", "dev.failed", "dev.blocked",
    "judge.failed", "verify.failed", "static_gate.failed",
    "ship.failed", "ship.blocked",
    "task.rework.requested", "task.rework.capped", "task.rework.blocked",
    "task.retry_requested", "task.retry_scheduled",
    "worker.stuck.recovered", "worker.stuck.recovery_failed",
    "autoresearch.bug_candidate.created",
    "autoresearch.repair.closeout.required",
    # Run Manager run-level escalation + human-decision lifecycle (backlog
    # 2026-06-25): monitor execution status + surface "needs human" on Feishu,
    # so the operator can reply (kanban_agent inbound) → ControlledAction.
    "human.escalation.sent",
    "human.escalation.acknowledged",
    "human.escalation.failed",
    "run.manager.human_decision.applied",
    "run.manager.human_decision.rejected",
}

# Event type → channel role mapping
_ROUTING: dict[str, str] = {
    "human.escalate": "approval",
    "worker.stuck": "approval",
    "cost.exceeded": "alert",
    "review.rejected": "alert",
    "test.failed": "alert",
    "task.done": "progress",
    "task.dispatched": "progress",
    "loop.completed": "progress",
    # G-RECYCLE-7
    "worker.context.warning": "approval",
    "worker.context.critical": "approval",
    "worker.recycling": "progress",
    "worker.recycled": "progress",
    "worker.recycle.failed": "approval",
    # G-WIRE-1/2/3
    "scope.violation": "approval",
    "worker.drift.detected": "approval",
    "worker.refresh.triggered": "progress",
    # G-COST-BLOCK-1
    "cost.budget.exceeded": "approval",
    # G-DISC-4
    "discriminator.failed": "approval",
    "discriminator.passed": "progress",
    # rework/failure/retry + autoresearch bug routing
    "integration.failed": "alert",
    "dev.failed": "alert",
    "judge.failed": "alert",
    "verify.failed": "alert",
    "static_gate.failed": "alert",
    "ship.failed": "alert",
    "autoresearch.bug_candidate.created": "alert",
    "task.rework.requested": "progress",
    "task.retry_requested": "progress",
    "task.retry_scheduled": "progress",
    "worker.stuck.recovered": "progress",
    "task.rework.capped": "approval",
    "task.rework.blocked": "approval",
    "dev.blocked": "approval",
    "ship.blocked": "approval",
    "worker.stuck.recovery_failed": "approval",
    "autoresearch.repair.closeout.required": "approval",
    # Run Manager: escalation needs a human decision (approval); the human-decision
    # verdict + acknowledgement are progress回执; a failed escalation delivery is an alert.
    "human.escalation.sent": "approval",
    "human.escalation.acknowledged": "progress",
    "human.escalation.failed": "alert",
    "run.manager.human_decision.applied": "progress",
    "run.manager.human_decision.rejected": "progress",
}


@dataclass
class RoutingConfig:
    channels: dict[str, str] = field(default_factory=dict)  # role -> chat_id
    receive_id_type: str = "chat_id"
    receive_id_types: dict[str, str] = field(default_factory=dict)

    def receive_id_type_for(self, channel_role: str) -> str:
        return self.receive_id_types.get(channel_role) or self.receive_id_type or "chat_id"


class ProjectionRouter:
    """Route events to Feishu channels as views."""

    def __init__(
        self,
        transport: FeishuTransport,
        routing: RoutingConfig,
        state_dir: Path,
    ) -> None:
        self.transport = transport
        self.routing = routing
        self.state_dir = state_dir
        self.task_store = TaskStore(state_dir / "kanban.json")

    def should_push(self, event: ZfEvent) -> bool:
        """Check if event warrants a push notification."""
        spec = spec_for_event(event.type)
        if spec is not None and spec.notification_policy:
            return spec.effective_notification_policy == "owner_immediate"
        return event.type in _MUST_PUSH

    def route_event(self, event: ZfEvent) -> bool:
        """Route an event to the appropriate Feishu channel."""
        if not self.should_push(event):
            return False

        channel_role = _ROUTING.get(event.type, "progress")
        resolved_role, chat_id = self._resolve_channel(channel_role)
        if not chat_id:
            return False

        content = self._format_event(event)
        message = FeishuMessage(
            chat_id=chat_id,
            content=content,
            receive_id_type=self.routing.receive_id_type_for(resolved_role),
        )
        return self.transport.send_message(message)

    def _resolve_channel(self, preferred_role: str) -> tuple[str, str]:
        """Resolve a configured Feishu route with owner fallback.

        Many local runs configure one owner target instead of separate
        approval/alert/progress chats. Escalation events should use that owner
        route instead of being silently dropped when the preferred role is
        absent.
        """

        seen: set[str] = set()
        for role in (preferred_role, "owner", "alert", "progress", "approval"):
            if not role or role in seen:
                continue
            seen.add(role)
            chat_id = self.routing.channels.get(role)
            if chat_id:
                return role, chat_id
        for role, chat_id in self.routing.channels.items():
            if chat_id:
                return role, chat_id
        return preferred_role, ""

    def build_summary(self) -> SummaryView:
        """Build a summary view from current state."""
        tasks = self.task_store.list_all_with_archive(last_days=30)
        return SummaryView(
            project="",
            done=sum(1 for t in tasks if t.status == "done"),
            in_progress=sum(1 for t in tasks if t.status == "in_progress"),
            blocked=sum(1 for t in tasks if t.status == "blocked"),
            backlog=sum(1 for t in tasks if t.status == "backlog"),
        )

    def build_task_view(self, task_id: str) -> TaskView | None:
        """Build a task view."""
        task = self.task_store.get(task_id)
        if task is None:
            return None
        return TaskView(
            task_id=task.id,
            title=task.title,
            status=task.status,
            assigned_to=task.assigned_to or "",
        )

    def _format_event(self, event: ZfEvent) -> str:
        """Format event as human-readable message."""
        parts = [f"[{event.type}]"]
        if event.task_id:
            parts.append(f"Task: {event.task_id}")
        if event.payload:
            for k, v in event.payload.items():
                parts.append(f"{k}: {v}")
        return " | ".join(parts)
