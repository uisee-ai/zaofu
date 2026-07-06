"""Unified agent-session stream event bridge."""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from zf.core.events import EventWriter, ZfEvent
from zf.core.security.redaction import redact_obj
from zf.runtime.agent_session_output import apply_agent_output_contract
from zf.runtime.live_delta_bus import live_delta_bus_for_writer


@dataclass(frozen=True)
class AgentSessionIdentity:
    run_id: str
    thread_id: str
    source: str
    actor: str
    task_id: str | None = None
    causation_id: str | None = None
    correlation_id: str | None = None
    project_id: str = ""
    conversation_id: str = ""
    channel_id: str = ""
    request_id: str = ""
    message_id: str = ""
    member_id: str = ""
    target_member_id: str = ""
    provider: str = ""
    backend: str = ""
    provider_session_id: str = ""
    snapshot_ref: str = ""


class AgentSessionStreamEmitter:
    """Map provider stream messages to canonical agent-session events."""

    def __init__(
        self,
        *,
        writer: EventWriter,
        identity: AgentSessionIdentity,
        flush_interval_s: float = 0.15,
        commit_final_text: bool = True,
    ) -> None:
        self.writer = writer
        self.identity = identity
        self.flush_interval_s = flush_interval_s
        # doc 106 guardrail: with deltas off-ledger, an UNWRAPPED run must
        # commit its aggregate text on run.completed. Wrapped runs (channel
        # message.posted / kanban.agent.reply carry the full text) opt out
        # to avoid double-committing the same body.
        self.commit_final_text = commit_final_text
        self.seq = 0
        self._started_event: ZfEvent | None = None
        self._pending_text: list[str] = []
        self._pending_thinking: list[str] = []
        self._final_text: list[str] = []
        self._last_flush_at = time.monotonic()
        self._content_started = False

    @property
    def started_event_id(self) -> str | None:
        return self._started_event.id if self._started_event is not None else None

    def start(
        self,
        *,
        provider_session_id: str = "",
        permission_snapshot: dict[str, Any] | None = None,
        permission_drift: dict[str, Any] | None = None,
    ) -> ZfEvent:
        self._started_event = self.writer.emit(
            "agent.session.run.started",
            actor=self.identity.actor,
            task_id=self.identity.task_id,
            causation_id=self.identity.causation_id,
            correlation_id=self.identity.correlation_id,
            payload=redact_obj({
                **self._base_payload(provider_session_id=provider_session_id),
                "status": "running",
                "permission_snapshot": permission_snapshot or {},
                "permission_drift": permission_drift or {},
            }),
        )
        return self._started_event

    def emit_message(self, message: Any) -> None:
        message_type = str(getattr(message, "type", "") or "")
        content = str(getattr(message, "content", "") or "")
        if message_type == "text":
            if content:
                self._pending_text.append(content)
                self._final_text.append(content)
            self._flush_first_content_or_due()
            return
        if message_type == "thinking":
            if content:
                self._pending_thinking.append(content)
            self._flush_first_content_or_due()
            return

        self.flush()
        self._emit_part(
            kind=_kind_for_message_type(message_type),
            state=_state_for_message_type(message_type),
            content=content or str(getattr(message, "output", "") or ""),
            refs=_refs_for_message(message),
        )

    def flush(self) -> None:
        if self._pending_thinking:
            content = "".join(self._pending_thinking)
            self._pending_thinking.clear()
            self._emit_part(kind="thinking", state="delta", content=content)
        if self._pending_text:
            content = "".join(self._pending_text)
            self._pending_text.clear()
            self._emit_part(kind="text", state="delta", content=content)
        self._last_flush_at = time.monotonic()

    def complete(
        self,
        *,
        status: str = "completed",
        reason: str = "",
        provider_session_id: str = "",
        usage: dict[str, Any] | None = None,
        permission_snapshot: dict[str, Any] | None = None,
        permission_drift: dict[str, Any] | None = None,
    ) -> ZfEvent:
        self.flush()
        payload = {
            **self._base_payload(provider_session_id=provider_session_id),
            "status": status,
            "reason": reason,
            "usage": usage or {},
            "permission_snapshot": permission_snapshot or {},
            "permission_drift": permission_drift or {},
        }
        # doc 106 hard guardrail: with deltas off-ledger, the aggregate text
        # must land in a committed event or an unwrapped run loses its final
        # assistant text entirely.
        if self._final_text and self.commit_final_text:
            payload["final_text"] = "".join(self._final_text)
            state_dir = getattr(getattr(self.writer, "event_log", None), "path", None)
            if state_dir is not None:
                payload = apply_agent_output_contract(
                    Path(state_dir).parent,
                    payload,
                    text_keys=("final_text",),
                    metadata={
                        "source": self.identity.source,
                        "producer": self.identity.actor,
                        "run_id": self.identity.run_id,
                        "thread_id": self.identity.thread_id,
                        "part_id": "final-text",
                        "kind": "text",
                        "project_id": self.identity.project_id,
                    },
                )
        return self.writer.emit(
            "agent.session.run.completed",
            actor=self.identity.actor,
            task_id=self.identity.task_id,
            causation_id=self.started_event_id or self.identity.causation_id,
            correlation_id=self.identity.correlation_id,
            payload=redact_obj(payload),
        )

    def fail(
        self,
        *,
        reason: str,
        status: str = "failed",
        provider_session_id: str = "",
        usage: dict[str, Any] | None = None,
        permission_snapshot: dict[str, Any] | None = None,
        permission_drift: dict[str, Any] | None = None,
    ) -> ZfEvent:
        self.flush()
        return self.writer.emit(
            "agent.session.run.failed",
            actor=self.identity.actor,
            task_id=self.identity.task_id,
            causation_id=self.started_event_id or self.identity.causation_id,
            correlation_id=self.identity.correlation_id,
            payload=redact_obj({
                **self._base_payload(provider_session_id=provider_session_id),
                "status": status,
                "reason": reason,
                "usage": usage or {},
                "permission_snapshot": permission_snapshot or {},
                "permission_drift": permission_drift or {},
            }),
        )

    def cancel(
        self,
        *,
        reason: str,
        provider_session_id: str = "",
    ) -> ZfEvent:
        self.flush()
        return self.writer.emit(
            "agent.session.run.cancelled",
            actor=self.identity.actor,
            task_id=self.identity.task_id,
            causation_id=self.started_event_id or self.identity.causation_id,
            correlation_id=self.identity.correlation_id,
            payload=redact_obj({
                **self._base_payload(provider_session_id=provider_session_id),
                "status": "cancelled",
                "reason": reason,
            }),
        )

    def _flush_if_due(self) -> None:
        if time.monotonic() - self._last_flush_at >= self.flush_interval_s:
            self.flush()

    def _flush_first_content_or_due(self) -> None:
        if not self._content_started:
            self._content_started = True
            self.flush()
            return
        self._flush_if_due()

    def _emit_part(
        self,
        *,
        kind: str,
        state: str,
        content: str = "",
        refs: dict[str, Any] | None = None,
    ) -> None:
        self.seq += 1
        part_id = f"{kind}-{self.seq:04d}"
        payload = {
            **self._base_payload(),
            "part_id": part_id,
            "kind": kind,
            "state": state,
            "delta": content,
            "content": content,
            "seq": self.seq,
            "refs": refs or {},
        }
        # doc 106 B axis: token deltas are UI transport, not truth. They go
        # to the ephemeral LiveDeltaBus (SSE merges them); the final text is
        # committed on run.completed / the wrapping domain event. Non-delta
        # parts (tool use etc.) stay in the ledger as interaction evidence.
        if state == "delta" and kind in {"text", "thinking"}:
            bus = live_delta_bus_for_writer(self.writer)
            if bus is not None:
                bus.publish(
                    "agent.session.part.delta",
                    payload,
                    key=self.identity.run_id or self.identity.thread_id or "run",
                    actor=self.identity.actor,
                    task_id=self.identity.task_id,
                    causation_id=self.started_event_id or self.identity.causation_id,
                    correlation_id=self.identity.correlation_id,
                )
                return
        state_dir = getattr(getattr(self.writer, "event_log", None), "path", None)
        if state_dir is not None:
            payload = apply_agent_output_contract(
                Path(state_dir).parent,
                payload,
                text_keys=("content", "delta"),
                metadata={
                    "source": self.identity.source,
                    "producer": self.identity.actor,
                    "run_id": self.identity.run_id,
                    "thread_id": self.identity.thread_id,
                    "part_id": part_id,
                    "kind": kind,
                    "seq": self.seq,
                    "project_id": self.identity.project_id,
                    "conversation_id": self.identity.conversation_id,
                    "channel_id": self.identity.channel_id,
                    "task_id": self.identity.task_id or "",
                },
            )
        self.writer.emit(
            "agent.session.part.delta",
            actor=self.identity.actor,
            task_id=self.identity.task_id,
            causation_id=self.started_event_id or self.identity.causation_id,
            correlation_id=self.identity.correlation_id,
            payload=redact_obj(payload),
        )

    def _base_payload(self, *, provider_session_id: str = "") -> dict[str, Any]:
        session_id = provider_session_id or self.identity.provider_session_id
        return {
            "run_id": self.identity.run_id,
            "thread_id": self.identity.thread_id,
            "source": self.identity.source,
            "project_id": self.identity.project_id,
            "conversation_id": self.identity.conversation_id,
            "channel_id": self.identity.channel_id,
            "request_id": self.identity.request_id,
            "message_id": self.identity.message_id,
            "member_id": self.identity.member_id,
            "target_member_id": self.identity.target_member_id,
            "provider": self.identity.provider or self.identity.backend,
            "backend": self.identity.backend,
            "provider_session_id": session_id,
            "snapshot_ref": self.identity.snapshot_ref,
            "runtime_snapshot_ref": self.identity.snapshot_ref,
        }


def _kind_for_message_type(message_type: str) -> str:
    if message_type in {"tool_use", "tool"}:
        return "tool"
    if message_type in {"tool_result", "result"}:
        return "tool_result"
    if message_type == "status":
        return "status"
    if message_type == "thinking":
        return "thinking"
    return "text" if message_type == "text" else message_type or "event"


def _state_for_message_type(message_type: str) -> str:
    if message_type == "status":
        return "started"
    if message_type in {"tool_result", "result"}:
        return "completed"
    return "delta"


def _refs_for_message(message: Any) -> dict[str, Any]:
    refs: dict[str, Any] = {}
    session_id = str(getattr(message, "session_id", "") or "")
    tool = str(getattr(message, "tool", "") or "")
    raw = getattr(message, "raw", {}) or {}
    value_input = getattr(message, "input", None)
    if session_id:
        refs["provider_session_id"] = session_id
    if tool:
        refs["tool"] = tool
    if value_input is not None:
        refs["input"] = value_input
    if isinstance(raw, dict) and raw:
        refs["raw"] = raw
    return refs


__all__ = ["AgentSessionIdentity", "AgentSessionStreamEmitter"]
