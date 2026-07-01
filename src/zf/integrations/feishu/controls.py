"""Feishu control actions — /zf pause, /zf resume, etc."""

from __future__ import annotations

from pathlib import Path

from zf.core.events import EventWriter
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.integrations.feishu.gateway import FeishuCommandEnvelope


class ControlHandler:
    """Execute control commands that modify harness state."""

    def __init__(self, state_dir: Path) -> None:
        self.state_dir = state_dir
        self.event_log = EventLog(state_dir / "events.jsonl")
        self.writer = EventWriter(self.event_log)
        self._queue: list[FeishuCommandEnvelope] = []
        self._processing = False

    def execute(self, envelope: FeishuCommandEnvelope) -> str:
        """Execute a control command. Returns confirmation text."""
        handlers = {
            "pause": self._pause,
            "resume": self._resume,
            "retry": self._retry,
            "cancel": self._cancel,
            "note": self._note,
        }
        handler = handlers.get(envelope.command)
        if handler is None:
            return f"Unknown control: {envelope.command}"

        # Serial execution queue
        self._queue.append(envelope)
        if self._processing:
            return "Command queued (another action in progress)."
        return self._process_queue()

    def _process_queue(self) -> str:
        """Process queued commands serially."""
        self._processing = True
        result = ""
        while self._queue:
            env = self._queue.pop(0)
            handlers = {
                "pause": self._pause,
                "resume": self._resume,
                "retry": self._retry,
                "cancel": self._cancel,
                "note": self._note,
            }
            handler = handlers.get(env.command, self._unknown)
            result = handler(env)
        self._processing = False
        return result

    def _pause(self, env: FeishuCommandEnvelope) -> str:
        self.writer.append(ZfEvent(
            type="loop.pause_requested", actor=f"feishu:{env.user_id}",
        ))
        return "Loop pause requested."

    def _resume(self, env: FeishuCommandEnvelope) -> str:
        self.writer.append(ZfEvent(
            type="loop.resume_requested", actor=f"feishu:{env.user_id}",
        ))
        return "Loop resume requested."

    def _retry(self, env: FeishuCommandEnvelope) -> str:
        task_id = env.args[0] if env.args else ""
        self.writer.append(ZfEvent(
            type="task.retry_requested", actor=f"feishu:{env.user_id}",
            task_id=task_id,
        ))
        return f"Retry requested for {task_id}."

    def _cancel(self, env: FeishuCommandEnvelope) -> str:
        task_id = env.args[0] if env.args else ""
        self.writer.append(ZfEvent(
            type="task.cancel_requested", actor=f"feishu:{env.user_id}",
            task_id=task_id,
        ))
        return f"Cancel requested for {task_id}."

    def _note(self, env: FeishuCommandEnvelope) -> str:
        text = " ".join(env.args) if env.args else ""
        self.writer.append(ZfEvent(
            type="human.note", actor=f"feishu:{env.user_id}",
            payload={"text": text},
        ))
        return "Note recorded."

    def _unknown(self, env: FeishuCommandEnvelope) -> str:
        return f"Unknown control: {env.command}"
