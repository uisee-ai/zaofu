"""Ephemeral streaming and terminal timing for Kanban Agent turns."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.core.security.redaction import redact_obj
from zf.runtime.agent_session_stream import AgentSessionStreamEmitter
from zf.runtime.live_delta_bus import live_delta_bus_for_writer
from zf.web.headless_agent import HeadlessMessage
from zf.web.headless_control_stream import HeadlessControlStreamFilter
from zf.web.headless_turn_outcome import classify_headless_turn


def classify_headless_result(result: Any) -> dict[str, Any]:
    return classify_headless_turn(
        status=getattr(result, "status", "failed"),
        error=getattr(result, "error", ""),
        incomplete=bool(getattr(result, "incomplete", False)),
        completion_reason=getattr(result, "completion_reason", ""),
    )


def classify_headless_failure(reason: object) -> dict[str, Any]:
    return classify_headless_turn(status="failed", error=reason)


class HeadlessDeltaEmitter:
    """Batch provider output into the ephemeral Kanban turn delta bus."""

    def __init__(
        self,
        *,
        writer: EventWriter,
        task_id: str | None,
        turn_started: ZfEvent,
        user_message: ZfEvent,
        turn_id: str,
        thread_key: str,
        project_id: str,
        conversation_id: str,
        backend: str,
        agent_session_emitter: AgentSessionStreamEmitter | None = None,
        commit_callback: Callable[[HeadlessMessage], None] | None = None,
        flush_interval_s: float = 0.15,
    ) -> None:
        self.writer = writer
        self.task_id = task_id
        self.turn_started = turn_started
        self.user_message = user_message
        self.turn_id = turn_id
        self.thread_key = thread_key
        self.project_id = project_id
        self.conversation_id = conversation_id
        self.backend = backend
        self.agent_session_emitter = agent_session_emitter
        self.commit_callback = commit_callback
        self.flush_interval_s = flush_interval_s
        self.delta_seq = 0
        self._pending_text: list[str] = []
        self._pending_thinking: list[str] = []
        self._last_flush_at = time.monotonic()
        self._content_started = False
        self._control_stream = HeadlessControlStreamFilter()
        self.first_output_at: float | None = None

    def emit(self, message: HeadlessMessage) -> None:
        if self.first_output_at is None:
            self.first_output_at = time.monotonic()
        if message.type == "text":
            batch = self._control_stream.feed(message.content)
            for content in batch.visible_text:
                visible = HeadlessMessage(type="text", content=content)
                if self.agent_session_emitter is not None:
                    self.agent_session_emitter.emit_message(visible)
                self._pending_text.append(content)
            self._flush_first_content_or_due()
            for control_kind in batch.preparing:
                self.flush()
                self._emit_one(HeadlessMessage(
                    type="status",
                    content=(
                        "Preparing choices..."
                        if control_kind == "plan_request"
                        else "Preparing action preview..."
                    ),
                    raw={"control_state": f"{control_kind}_buffering"},
                ))
            return
        if self.agent_session_emitter is not None:
            self.agent_session_emitter.emit_message(message)
        if message.type == "thinking":
            if message.content:
                self._pending_thinking.append(message.content)
            self._flush_first_content_or_due()
            return
        self.flush()
        self._emit_one(message)

    def flush(self) -> None:
        if self.agent_session_emitter is not None:
            self.agent_session_emitter.flush()
        if self._pending_thinking:
            content = "".join(self._pending_thinking)
            self._pending_thinking.clear()
            self._emit_one(HeadlessMessage(type="thinking", content=content))
        if self._pending_text:
            content = "".join(self._pending_text)
            self._pending_text.clear()
            self._emit_one(HeadlessMessage(type="text", content=content))
        self._last_flush_at = time.monotonic()

    def _flush_if_due(self) -> None:
        if time.monotonic() - self._last_flush_at >= self.flush_interval_s:
            self.flush()

    def _flush_first_content_or_due(self) -> None:
        if not self._content_started:
            self._content_started = True
            self.flush()
            return
        self._flush_if_due()

    def _emit_one(self, message: HeadlessMessage) -> None:
        # Turn deltas are ephemeral UI transport. The committed reply remains
        # in events.jsonl and is sufficient to rebuild completed history.
        self.delta_seq += 1
        if self.commit_callback is not None:
            self.commit_callback(message)
        bus = live_delta_bus_for_writer(self.writer)
        if bus is None:
            return
        bus.publish(
            "kanban.agent.turn.delta",
            {
                "turn_id": self.turn_id,
                "thread_key": self.thread_key,
                "project_id": self.project_id,
                "conversation_id": self.conversation_id,
                "backend": self.backend,
                "seq": self.delta_seq,
                **_message_event_payload(message),
            },
            key=self.turn_id,
            actor="web",
            task_id=self.task_id,
            causation_id=self.turn_started.id,
            correlation_id=self.user_message.correlation_id,
        )


def kanban_turn_timing_payload(
    *,
    turn_started_at: float,
    context_started_at: float,
    context_completed_at: float,
    provider_started_at: float,
    provider_completed_at: float,
    first_output_at: float | None,
    plan_started_at: float | None = None,
    plan_completed_at: float | None = None,
) -> dict[str, Any]:
    terminal_at = time.monotonic()

    def elapsed_ms(start: float, end: float) -> int:
        return max(0, int((end - start) * 1000))

    return {
        "duration_ms": elapsed_ms(turn_started_at, terminal_at),
        "timing": {
            "context_duration_ms": elapsed_ms(
                context_started_at,
                context_completed_at,
            ),
            "provider_duration_ms": elapsed_ms(
                provider_started_at,
                provider_completed_at,
            ),
            "time_to_first_output_ms": (
                elapsed_ms(provider_started_at, first_output_at)
                if first_output_at is not None
                else None
            ),
            "plan_projection_duration_ms": (
                elapsed_ms(plan_started_at, plan_completed_at)
                if plan_started_at is not None
                and plan_completed_at is not None
                else None
            ),
        },
    }


def _message_event_payload(message: HeadlessMessage) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "message_type": message.type,
        "content": message.content,
        "session_id": message.session_id,
        "tool": message.tool,
    }
    if message.input is not None:
        payload["input"] = redact_obj(message.input)
    if message.output:
        payload["output"] = message.output
    tool_outcome = message.raw.get("tool_outcome")
    if message.type == "tool_result" and isinstance(tool_outcome, str) and tool_outcome in {"completed", "failed", "cancelled", "unknown"}:
        payload["status"] = tool_outcome
    call_id = message.raw.get("call_id")
    if message.type in {"tool_use", "tool_result"} and isinstance(call_id, str) and 0 < len(call_id) <= 256:
        payload["tool_call_id"] = call_id
    control_state = str(message.raw.get("control_state") or "")
    if control_state:
        payload["control_state"] = control_state
    return payload
