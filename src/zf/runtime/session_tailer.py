"""Session-jsonl tailer — emit agent.* events from claude's local files.

Every interactive Claude CLI session writes its message stream to
``~/.claude/projects/<escaped-cwd>/<session-uuid>.jsonl`` — the same
shape that ``claude --output-format stream-json`` emits over stdout.
This tailer subscribes to those files and converts new lines into
ZfEvents, giving a tmux-hosted orchestrator / worker the same agent-
level telemetry as the stream-json transport used to provide, without
touching Anthropic's headless rate-limit quota.

Scope of Phase 2: Claude only. CodexSessionTailer is Phase 3.

Events emitted (actor = instance_id, resolved up-front via registry):
  - assistant / thinking   → agent.thinking
  - assistant / tool_use   → agent.tool.use
  - assistant / text       → agent.text
  - user     / tool_result → agent.tool.result

Messages we skip (CLI-internal bookkeeping):
  permission-mode, file-history-snapshot, attachment, last-prompt,
  system, ai-title, queue-operation, summary
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent


_POLL_INTERVAL_S = 0.5
_SKIP_TYPES = {
    "permission-mode",
    "file-history-snapshot",
    "attachment",
    "last-prompt",
    "system",
    "ai-title",
    "queue-operation",
    "summary",
}


class _BaseSessionTailer:
    """Shared tail loop for claude / codex session JSONL files.

    Subclasses override ``_emit_from_line`` to parse their backend-
    specific schema. File discovery, EOF tracking, partial-line
    handling, and thread lifecycle are common.
    """

    def __init__(self, event_log: EventLog) -> None:
        self._event_log = event_log
        self._threads: dict[str, threading.Thread] = {}
        self._stopping = threading.Event()

    def tail(
        self, instance_id: str, session_path: Path,
    ) -> None:
        """Start tailing session_path for the given role instance.

        If already tailing, no-op. If the file doesn't exist yet the
        thread waits (Claude writes the file only on first turn).
        """
        if instance_id in self._threads:
            return
        initial_offset = 0
        if session_path.exists():
            try:
                initial_offset = session_path.stat().st_size
            except OSError:
                initial_offset = 0
        t = threading.Thread(
            target=self._tail_loop,
            args=(instance_id, session_path, initial_offset),
            name=f"SessionTailer-{instance_id}",
            daemon=True,
        )
        self._threads[instance_id] = t
        t.start()

    def stop(self) -> None:
        self._stopping.set()
        for t in self._threads.values():
            t.join(timeout=1.0)

    def _tail_loop(
        self,
        instance_id: str,
        session_path: Path,
        initial_offset: int,
    ) -> None:
        """Main tail loop: open file when available, track offset, emit."""
        # If the file already exists when tail() is called, begin at EOF so
        # restart doesn't double-emit history. If it is created immediately
        # after tail() returns, start at 0; computing this inside the thread is
        # racy and can skip the first fresh line.
        offset = initial_offset
        while not self._stopping.is_set():
            try:
                if session_path.exists():
                    offset = self._drain(instance_id, session_path, offset)
            except Exception:
                # Never abort the tailer on a single-line parse error;
                # best-effort telemetry is the whole point.
                pass
            if self._stopping.wait(_POLL_INTERVAL_S):
                return

    def _drain(
        self, instance_id: str, path: Path, offset: int,
    ) -> int:
        """Read any new bytes since offset; emit events; return new offset."""
        try:
            size = path.stat().st_size
        except OSError:
            return offset
        if size < offset:
            # File was truncated / rotated; reset and skip current content.
            return size
        if size == offset:
            return offset
        try:
            with path.open("r", encoding="utf-8", errors="replace") as f:
                f.seek(offset)
                data = f.read(size - offset)
                new_offset = f.tell()
        except OSError:
            return offset
        # Process complete lines only; keep any trailing partial line
        # by pushing offset back to the start of it.
        lines = data.splitlines(keepends=True)
        for line in lines:
            if not line.endswith("\n"):
                # Partial trailing line — rewind so next poll picks it up.
                new_offset -= len(line.encode("utf-8"))
                break
            self._emit_from_line(instance_id, line.strip())
        return new_offset

    def _emit_from_line(self, instance_id: str, raw: str) -> None:
        raise NotImplementedError

    def _append(self, event_type: str, actor: str, payload: dict) -> None:
        try:
            self._event_log.append(ZfEvent(
                type=event_type,
                actor=actor,
                payload=payload,
            ))
        except Exception:
            # Tailer events are telemetry; swallowing preserves main loop.
            pass


class ClaudeSessionTailer(_BaseSessionTailer):
    """Tail Claude Code session JSONL and emit agent.* events."""

    def _emit_from_line(self, instance_id: str, raw: str) -> None:
        if not raw:
            return
        try:
            m = json.loads(raw)
        except json.JSONDecodeError:
            return
        mtype = m.get("type")
        if mtype in _SKIP_TYPES or mtype is None:
            return

        msg = m.get("message")
        if not isinstance(msg, dict):
            return
        content = msg.get("content")
        if not isinstance(content, list):
            return

        for block in content:
            if not isinstance(block, dict):
                continue
            btype = block.get("type")
            if btype == "thinking":
                text = block.get("thinking", "") or ""
                if text.strip():
                    self._append(
                        "agent.thinking", instance_id,
                        {"text": text[:2000]},
                    )
            elif btype == "text":
                text = block.get("text", "") or ""
                if text.strip():
                    self._append(
                        "agent.text", instance_id,
                        {"text": text[:2000],
                         "stop_reason": msg.get("stop_reason", "")},
                    )
            elif btype == "tool_use":
                self._append(
                    "agent.tool.use", instance_id,
                    {
                        "tool": block.get("name", ""),
                        "tool_use_id": block.get("id", ""),
                        "input": _truncate(block.get("input", {}), 2000),
                    },
                )
            elif btype == "tool_result":
                self._append(
                    "agent.tool.result", instance_id,
                    {
                        "tool_use_id": block.get("tool_use_id", ""),
                        "is_error": bool(block.get("is_error", False)),
                        "content": _truncate(block.get("content", ""), 2000),
                    },
                )


class CodexSessionTailer(_BaseSessionTailer):
    """Tail Codex rollout JSONL and emit agent.* events.

    Codex wraps its messages in ``response_item`` objects with a
    nested ``payload`` block whose ``type`` distinguishes the kind:

        payload.type == "reasoning"             → agent.thinking
        payload.type == "function_call"         → agent.tool.use
        payload.type == "function_call_output"  → agent.tool.result
        payload.type == "message" + role=assistant → agent.text

    User messages and event_msg entries (token_count / task_started /
    task_complete / turn_context) are CLI bookkeeping and get skipped.
    Cost telemetry is handled by CodexSessionReader.
    """

    def _emit_from_line(self, instance_id: str, raw: str) -> None:
        if not raw:
            return
        try:
            m = json.loads(raw)
        except json.JSONDecodeError:
            return
        if m.get("type") != "response_item":
            return  # skip session_meta / event_msg / turn_context / ...
        payload = m.get("payload")
        if not isinstance(payload, dict):
            return
        ptype = payload.get("type")

        if ptype == "reasoning":
            # Codex puts reasoning text under summary[].text in some
            # versions and under content[].text in others.
            text = ""
            for candidate_key in ("summary", "content"):
                chunks = payload.get(candidate_key)
                if isinstance(chunks, list):
                    for c in chunks:
                        if isinstance(c, dict):
                            text += c.get("text", "") or c.get("reasoning", "") or ""
            if text.strip():
                self._append(
                    "agent.thinking", instance_id,
                    {"text": text[:2000]},
                )
        elif ptype == "function_call":
            self._append(
                "agent.tool.use", instance_id,
                {
                    "tool": payload.get("name", ""),
                    "tool_use_id": payload.get("call_id", ""),
                    "input": _truncate(payload.get("arguments", ""), 2000),
                },
            )
        elif ptype == "function_call_output":
            output = payload.get("output", "")
            if isinstance(output, dict):
                is_err = bool(output.get("is_error") or output.get("error"))
                content = output.get("content") or output.get("text") or ""
            else:
                is_err = False
                content = output
            self._append(
                "agent.tool.result", instance_id,
                {
                    "tool_use_id": payload.get("call_id", ""),
                    "is_error": is_err,
                    "content": _truncate(content, 2000),
                },
            )
        elif ptype == "message":
            # Assistant text only; skip user / developer / system
            role = payload.get("role", "")
            if role != "assistant":
                return
            text = ""
            content = payload.get("content")
            if isinstance(content, list):
                for c in content:
                    if isinstance(c, dict):
                        text += c.get("text", "") or c.get("output_text", "") or ""
            if text.strip():
                self._append(
                    "agent.text", instance_id,
                    {"text": text[:2000], "stop_reason": ""},
                )


def _truncate(obj: object, limit: int) -> str:
    s = obj if isinstance(obj, str) else json.dumps(obj, ensure_ascii=False, default=str)
    return s[:limit]


def claude_session_path(project_root: str, session_uuid: str) -> Path:
    """Compute ~/.claude/projects/<escaped-cwd>/<session_uuid>.jsonl.

    Claude escapes the project_root path by replacing '/' with '-' and
    prepending '-'. Mirrors the logic in StreamJsonTransport._session_
    exists_on_disk so we're looking at the same file Claude writes.
    """
    escaped = "-" + project_root.lstrip("/").replace("/", "-")
    return Path.home() / ".claude" / "projects" / escaped / f"{session_uuid}.jsonl"


def codex_session_path(session_uuid: str) -> Path | None:
    """Find the codex rollout JSONL for the given uuid, or None.

    Codex writes to ``~/.codex/sessions/<Y>/<M>/<D>/rollout-<ISO>-<uuid>.jsonl``
    and the date folder is whichever day the session was spawned. We
    glob all dates and pick the newest file containing the uuid —
    matches how CodexSessionReader.session_path does lookup.
    """
    import glob as _glob
    pattern = str(Path.home() / ".codex" / "sessions" / "*" / "*" / "*"
                  / f"rollout-*-{session_uuid}.jsonl")
    matches = sorted(_glob.glob(pattern))
    if not matches:
        return None
    return Path(matches[-1])
