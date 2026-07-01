"""Feishu query handlers — /zf status, /zf tasks, etc."""

from __future__ import annotations

from pathlib import Path

from zf.core.events.log import EventLog
from zf.core.task.store import TaskStore
from zf.core.cost.tracker import CostTracker
from zf.integrations.feishu.gateway import FeishuCommandEnvelope


class QueryExecutor:
    """Execute query commands and return formatted responses."""

    def __init__(self, state_dir: Path) -> None:
        self.state_dir = state_dir
        self.task_store = TaskStore(state_dir / "kanban.json")
        self.event_log = EventLog(state_dir / "events.jsonl")
        self.cost_tracker = CostTracker(state_dir / "cost.jsonl")

    def execute(self, envelope: FeishuCommandEnvelope) -> str:
        """Execute a query command, return response text."""
        handlers = {
            "status": self._status,
            "tasks": self._tasks,
            "task": self._task_detail,
            "cost": self._cost,
            "blockers": self._blockers,
            "handoff": self._handoff,
        }
        handler = handlers.get(envelope.command)
        if handler is None:
            return f"Unknown query: {envelope.command}"
        return handler(envelope)

    def _status(self, env: FeishuCommandEnvelope) -> str:
        tasks = self.task_store.list_all_with_archive(last_days=30)
        done = sum(1 for t in tasks if t.status == "done")
        active = sum(1 for t in tasks if t.status == "in_progress")
        total = len(tasks)
        return f"Tasks: {total} total, {active} active, {done} done"

    def _tasks(self, env: FeishuCommandEnvelope) -> str:
        tasks = self.task_store.list_all_with_archive(last_days=30)
        if not tasks:
            return "No tasks."
        lines = [f"{t.status:15s} {t.id}  {t.title}" for t in tasks]
        return "\n".join(lines)

    def _task_detail(self, env: FeishuCommandEnvelope) -> str:
        if not env.args:
            return "Usage: /zf task <TASK-ID>"
        task = self.task_store.get(env.args[0])
        if task is None:
            return f"Task {env.args[0]} not found."
        return (f"ID: {task.id}\nTitle: {task.title}\n"
                f"Status: {task.status}\nAssigned: {task.assigned_to or 'none'}")

    def _cost(self, env: FeishuCommandEnvelope) -> str:
        total = self.cost_tracker.total_usd()
        return f"Total cost: ${total:.4f}"

    def _blockers(self, env: FeishuCommandEnvelope) -> str:
        tasks = self.task_store.filter(status="blocked")
        if not tasks:
            return "No blocked tasks."
        lines = [f"{t.id}: {t.title}" for t in tasks]
        return "\n".join(lines)

    def _handoff(self, env: FeishuCommandEnvelope) -> str:
        status = self._status(env)
        events = self.event_log.query(last=5)
        if not events:
            return f"{status}\nRecent events: none"
        lines = [status, "Recent events:"]
        for event in events:
            task = f" {event.task_id}" if event.task_id else ""
            lines.append(f"- {event.type}{task} by {event.actor or 'unknown'}")
        return "\n".join(lines)
