"""BackendSessionReader — unified disk reader for agent session files.

Both Claude Code and Codex CLI persist per-turn conversation state
to disk regardless of how they were invoked (tmux pane vs headless
SDK). This module exposes a single abstraction that extracts the
"current context window utilisation" for an instance without caring
about the transport layer.

Used by Sprint E's context recycle decision logic, and by the cost
tracker (as the canonical data source for tmux-hosted workers that
have no SDK ResultMessage stream to consume).

Path conventions:

    Claude: ~/.claude/projects/<cwd-with-/-as->/<uuid>.jsonl
        JSONL with mixed `type` entries; usage lives in
        ``assistant.message.usage`` with separate fields for
        fresh input / cache_read / cache_creation.

    Codex:  ~/.codex/sessions/<YYYY>/<MM>/<DD>/rollout-<ISO>-<uuid>.jsonl
        JSONL with `type` at top level; usage lives in the
        ``event_msg`` payload when ``payload.type == "token_count"``
        and ``payload.info`` is populated. Codex self-reports
        ``info.model_context_window``.

The two backends report slightly different usage semantics:

- Claude ``input_tokens`` is *fresh* (cache_read is a separate field)
- Codex ``input_tokens`` already *includes* cache

The readers normalise both into a single ``effective_input_tokens``
number representing "how much of the context window is currently
loaded", so downstream callers (ratio check, recycle trigger) don't
care which backend produced the report.
"""

from __future__ import annotations

import glob
import json
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path


# Patchable in tests
CODEX_SESSIONS_ROOT = Path.home() / ".codex" / "sessions"


@dataclass
class ConversationTurn:
    role: str  # "user" or "assistant"
    content: str
    line_offset: int
    timestamp: str = ""


@dataclass(frozen=True)
class TranscriptCatchup:
    """ZF-PWF-CATCHUP-001 (doc 41 §4.7): summary of what happened in
    a worker's transcript after a given timestamp (typically the
    timestamp of the last State Packet write).

    Used by the recovery briefing builder to inject "since the last
    snapshot you did X, the user said Y, and Z errored" as evidence —
    *not* as runtime truth. Truth lives in events.jsonl and the
    State Packet; this summary is only auditable context.
    """

    instance_id: str
    since_timestamp: str
    new_user_messages: tuple[str, ...] = ()
    new_assistant_excerpts: tuple[str, ...] = ()
    new_tool_uses: tuple[str, ...] = ()
    new_errors: tuple[str, ...] = ()
    new_edits: tuple[str, ...] = ()
    transcript_size_bytes: int = 0
    backend: str = ""


@dataclass
class UsageReport:
    """Normalised usage snapshot from either backend."""

    effective_input_tokens: int
    """Tokens currently loaded in the model's context window
    (fresh input + cache_read + cache_creation for Claude;
    last_token_usage.input_tokens for Codex)."""

    output_tokens: int
    """Tokens the model produced in its most recent turn."""

    model_context_window: int
    """Maximum context window size for the current model. Claude
    readers use the caller-supplied fallback_window; Codex readers
    take this from info.model_context_window in the session file."""

    ratio: float
    """effective_input_tokens / model_context_window (0.0–1.0)."""

    timestamp: str
    """ISO-8601 timestamp of the most recent turn."""

    raw: dict = field(default_factory=dict)
    """Raw usage dict, passed through for downstream (cost tracker)."""

    model: str = ""
    """Model id of the most recent turn (e.g. ``claude-opus-4-8``). Carried
    so the cost tracker can pick a per-model rate on the token-priced
    fallback path; empty when the session file doesn't report one."""


# Claude 4.x large-context models report a 1M context window. The 200k
# default fallback otherwise mis-flags a healthy worker (e.g. 247k tokens on
# opus-4-8) as 1.24x over-capacity → false context.warning + premature recycle.
_MODEL_CONTEXT_WINDOWS: tuple[tuple[str, int], ...] = (
    ("claude-opus-4", 1_000_000),
    ("claude-sonnet-4", 1_000_000),
)


def _window_for_model(model: str, fallback: int) -> int:
    """Return the model's real context window (>= fallback), by name prefix."""
    for prefix, window in _MODEL_CONTEXT_WINDOWS:
        if model.startswith(prefix):
            return max(fallback, window)
    return fallback


class BackendSessionReader(ABC):
    @abstractmethod
    def session_path(
        self,
        project_root: str,
        session_id: str,
        *,
        cached_path: Path | None = None,
    ) -> Path | None:
        """Return the disk path for this session, or None if unknown."""

    @abstractmethod
    def read_latest_usage(
        self,
        session_path: Path,
        *,
        fallback_window: int | None = None,
    ) -> UsageReport | None:
        """Return the most recent turn's usage. None if the file is
        missing or contains no usable usage data."""

    def scan_narrative_since(
        self,
        session_path: Path,
        *,
        since_timestamp: str,
        instance_id: str = "",
    ) -> TranscriptCatchup | None:
        """ZF-PWF-CATCHUP-001 (doc 41 §4.7): scan the worker's
        transcript file for events that happened after
        ``since_timestamp`` (typically the timestamp of the most
        recent State Packet write).

        Default implementation returns ``None`` — backends without a
        readable transcript format (Mock / Codex if rollout file is
        missing) simply opt out. Claude reader overrides with a real
        scan via the existing ``read_turns`` pipeline.

        Returns a :class:`TranscriptCatchup` describing what the
        recovery briefing builder should mention as evidence. The
        result is **never** treated as truth — it's surfaced for
        human / operator review only.
        """
        return None


class ClaudeSessionReader(BackendSessionReader):
    def __init__(self, projects_root: Path | None = None) -> None:
        self.projects_root = projects_root or (Path.home() / ".claude" / "projects")

    def session_path(
        self,
        project_root: str,
        session_id: str,
        *,
        cached_path: Path | None = None,
    ) -> Path | None:
        if cached_path is not None and cached_path.exists():
            return cached_path
        # Claude's project dir is the cwd with "/" AND "." replaced by "-"
        # and a leading "-" (for the leading slash). The "." matters for
        # worktree cwds like ".../hermes-agent/.zf-cj-min-refactor/..." whose
        # dir is "...hermes-agent--zf-cj-min-refactor-..." — without it the
        # disk reader never finds the session and claude-code roles report no
        # usage to the cost tracker.
        escaped = "-" + project_root.lstrip("/").replace("/", "-").replace(".", "-")
        path = self.projects_root / escaped / f"{session_id}.jsonl"
        if path.exists():
            return path
        # B-COST-02 R-3: self-healing fallback (mirrors CodexSessionReader).
        # The escaped-dir derivation must match Claude's on-disk naming
        # exactly; if it ever drifts, a uuid glob still recovers the session
        # so claude-code roles don't silently report zero usage. The uuid is
        # unique across project dirs, so the first match is authoritative.
        if session_id:
            matches = sorted(
                glob.glob(str(self.projects_root / "*" / f"{session_id}.jsonl"))
            )
            if matches:
                return Path(matches[-1])
        return None

    def read_latest_usage(
        self,
        session_path: Path,
        *,
        fallback_window: int | None = None,
    ) -> UsageReport | None:
        if not session_path.exists():
            return None
        window = fallback_window or 200_000
        latest: dict | None = None
        latest_ts: str = ""
        latest_model: str = ""
        try:
            text = session_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if obj.get("type") != "assistant":
                continue
            usage = obj.get("message", {}).get("usage")
            if not isinstance(usage, dict):
                continue
            latest = usage
            latest_ts = obj.get("timestamp", "")
            latest_model = obj.get("message", {}).get("model", "") or latest_model
        if latest is None:
            return None
        # Use the model's real context window so a healthy worker isn't
        # mis-flagged over-capacity: claude-opus-4-8 / sonnet-4.x have a 1M
        # window, but the 200k fallback makes a 247k worker read as 1.24x →
        # false context.warning + premature recycle churn.
        window = _window_for_model(latest_model, window)
        input_tokens = int(latest.get("input_tokens", 0))
        cache_read = int(latest.get("cache_read_input_tokens", 0))
        cache_creation = int(latest.get("cache_creation_input_tokens", 0))
        effective = input_tokens + cache_read + cache_creation
        output_tokens = int(latest.get("output_tokens", 0))
        ratio = effective / window if window > 0 else 0.0
        return UsageReport(
            effective_input_tokens=effective,
            output_tokens=output_tokens,
            model_context_window=window,
            ratio=ratio,
            timestamp=latest_ts,
            raw=dict(latest),
            model=latest_model,
        )

    def scan_narrative_since(
        self,
        session_path: Path,
        *,
        since_timestamp: str,
        instance_id: str = "",
    ) -> TranscriptCatchup | None:
        """ZF-PWF-CATCHUP-001 — Claude implementation.

        Reads the JSONL session file once, partitions records by
        ``timestamp > since_timestamp``, and extracts:

        - user messages → ``new_user_messages``
        - assistant text excerpts (head 200 chars) → ``new_assistant_excerpts``
        - tool_use blocks → ``new_tool_uses`` (formatted ``tool_name(...)``)
        - Edit / Write / MultiEdit tool_use → ``new_edits`` (paths)
        - ``error`` records or tool_result with is_error=True → ``new_errors``

        Returns ``None`` only when the file does not exist; an empty
        catchup is still a valid signal ("nothing happened since the
        snapshot, ok to compact").
        """
        if not session_path.exists():
            return None
        try:
            text = session_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None

        user_messages: list[str] = []
        assistant_excerpts: list[str] = []
        tool_uses: list[str] = []
        errors: list[str] = []
        edits: list[str] = []
        size = len(text.encode("utf-8", errors="replace"))

        threshold = since_timestamp or ""
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            ts = str(obj.get("timestamp", "") or "")
            if threshold and ts and ts <= threshold:
                continue
            msg_type = obj.get("type", "")
            message = obj.get("message", {}) or {}
            if msg_type == "human":
                content = message.get("content", "")
                if isinstance(content, str) and content:
                    user_messages.append(content[:400])
            elif msg_type == "assistant":
                blocks = message.get("content", [])
                if not isinstance(blocks, list):
                    continue
                for block in blocks:
                    if not isinstance(block, dict):
                        continue
                    btype = block.get("type", "")
                    if btype == "text":
                        excerpt = str(block.get("text", "") or "")[:200]
                        if excerpt:
                            assistant_excerpts.append(excerpt)
                    elif btype == "tool_use":
                        tool_name = str(block.get("name", "") or "")
                        if tool_name:
                            tool_uses.append(f"{tool_name}(...)")
                        # Edit / Write / MultiEdit → extract path
                        if tool_name in {"Edit", "Write", "MultiEdit"}:
                            inp = block.get("input", {}) or {}
                            if isinstance(inp, dict):
                                path = str(
                                    inp.get("file_path") or inp.get("path") or ""
                                )
                                if path:
                                    edits.append(path)
            elif msg_type == "error":
                err = str(message.get("content", "") or "")[:300]
                if err:
                    errors.append(err)
            # Some clients log tool_result with is_error=True
            if msg_type == "user":
                content = message.get("content", [])
                if isinstance(content, list):
                    for item in content:
                        if (
                            isinstance(item, dict)
                            and item.get("type") == "tool_result"
                            and item.get("is_error")
                        ):
                            errors.append(str(item.get("content", ""))[:300])

        return TranscriptCatchup(
            instance_id=instance_id,
            since_timestamp=since_timestamp,
            new_user_messages=tuple(user_messages),
            new_assistant_excerpts=tuple(assistant_excerpts),
            new_tool_uses=tuple(tool_uses),
            new_errors=tuple(errors),
            new_edits=tuple(edits),
            transcript_size_bytes=size,
            backend="claude-code",
        )

    def read_turns(
        self,
        session_path: Path,
        *,
        since_offset: int = 0,
    ) -> list[ConversationTurn]:
        if not session_path.exists():
            return []
        try:
            text = session_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return []
        turns: list[ConversationTurn] = []
        for i, line in enumerate(text.splitlines()):
            if i < since_offset:
                continue
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            msg_type = obj.get("type", "")
            ts = obj.get("timestamp", "")
            message = obj.get("message", {})
            if msg_type == "human":
                content = message.get("content", "")
                if isinstance(content, str):
                    turns.append(ConversationTurn(
                        role="user", content=content,
                        line_offset=i, timestamp=ts,
                    ))
            elif msg_type == "assistant":
                content_blocks = message.get("content", [])
                text_parts = []
                if isinstance(content_blocks, list):
                    for block in content_blocks:
                        if isinstance(block, dict) and block.get("type") == "text":
                            text_parts.append(block.get("text", ""))
                content_str = "\n".join(text_parts)
                if content_str:
                    turns.append(ConversationTurn(
                        role="assistant", content=content_str,
                        line_offset=i, timestamp=ts,
                    ))
        return turns


class CodexSessionReader(BackendSessionReader):
    def session_path(
        self,
        project_root: str,
        session_id: str,
        *,
        cached_path: Path | None = None,
    ) -> Path | None:
        if cached_path is not None and cached_path.exists():
            return cached_path
        # UUID-based lookup (observe_codex_session already cached the
        # uuid via RoleSessionRegistry).
        if session_id:
            pattern = str(
                CODEX_SESSIONS_ROOT / "*" / "*" / "*"
                / f"rollout-*-{session_id}.jsonl"
            )
            matches = sorted(glob.glob(pattern))
            if matches:
                return Path(matches[-1])
        # B-1203-06 R-3: self-healing fallback — glob by cwd. When the
        # harness runs ``_check_context_thresholds`` before observe has
        # had time to cache the uuid (early-boot, session_id == ""), or
        # when the cached uuid is stale (rare), a cwd-based lookup
        # recovers the rollout file for this project. Reads each
        # candidate's first-line session_meta; returns the newest
        # matching file. Cost tracking stays alive even during the
        # observe→cache window.
        return self._glob_by_cwd(project_root)

    def _glob_by_cwd(self, project_root: str) -> Path | None:
        pattern = str(
            CODEX_SESSIONS_ROOT / "*" / "*" / "*" / "rollout-*.jsonl"
        )
        try:
            project_path = Path(project_root).resolve()
        except OSError:
            project_path = Path(project_root)
        candidates: list[tuple[float, Path]] = []
        for raw in glob.glob(pattern):
            p = Path(raw)
            try:
                mtime = p.stat().st_mtime
            except OSError:
                continue
            if not self._rollout_matches_project(p, project_path):
                continue
            candidates.append((mtime, p))
        if not candidates:
            return None
        candidates.sort(key=lambda x: x[0], reverse=True)
        return candidates[0][1]

    @staticmethod
    def _rollout_matches_project(
        path: Path, project_path: Path,
    ) -> bool:
        try:
            with path.open("r", encoding="utf-8") as f:
                first = f.readline().strip()
            if not first:
                return False
            data = json.loads(first)
        except (OSError, json.JSONDecodeError):
            return False
        if not isinstance(data, dict):
            return False
        payload = data.get("payload") or {}
        rollout_cwd = payload.get("cwd", "") if isinstance(payload, dict) else ""
        if not rollout_cwd:
            return False
        try:
            return Path(rollout_cwd).resolve() == project_path
        except OSError:
            return str(rollout_cwd) == str(project_path)

    def read_latest_usage(
        self,
        session_path: Path,
        *,
        fallback_window: int | None = None,
    ) -> UsageReport | None:
        if not session_path.exists():
            return None
        latest_info: dict | None = None
        latest_ts: str = ""
        try:
            text = session_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if obj.get("type") != "event_msg":
                continue
            payload = obj.get("payload") or {}
            if payload.get("type") != "token_count":
                continue
            info = payload.get("info")
            if not isinstance(info, dict):
                continue
            latest_info = info
            latest_ts = obj.get("timestamp", "")
        if latest_info is None:
            return None
        last_usage = latest_info.get("last_token_usage") or {}
        effective = int(last_usage.get("input_tokens", 0))
        output_tokens = int(last_usage.get("output_tokens", 0))
        # Codex self-reports window; fallback only if missing
        window = int(latest_info.get("model_context_window", 0)) or (
            fallback_window or 258_400
        )
        ratio = effective / window if window > 0 else 0.0
        return UsageReport(
            effective_input_tokens=effective,
            output_tokens=output_tokens,
            model_context_window=window,
            ratio=ratio,
            timestamp=latest_ts,
            raw=dict(last_usage),
            model=str(latest_info.get("model", "")),
        )


_READERS: dict[str, BackendSessionReader] = {
    "claude-code": ClaudeSessionReader(),
    "codex": CodexSessionReader(),
}


def get_reader_for_backend(backend: str) -> BackendSessionReader | None:
    """Return a session reader for the given backend, or None if the
    backend has no disk session concept (mock / python)."""
    return _READERS.get(backend)


_mirror_offsets: dict[tuple[str, str], int] = {}


def mirror_transcript(
    *,
    reader: ClaudeSessionReader,
    session_path: Path,
    event_log: "EventLog",
    role: str,
) -> int:
    from zf.core.events.model import ZfEvent

    cache_key = (role, str(session_path))
    last_offset = _mirror_offsets.get(cache_key, 0)
    turns = reader.read_turns(session_path, since_offset=last_offset)
    if not turns:
        return 0
    count = 0
    for turn in turns:
        event_type = f"agent.turn.{turn.role}"
        try:
            event_log.append(ZfEvent(
                type=event_type,
                actor=role,
                payload={
                    "content": turn.content[:2000],
                    "line_offset": turn.line_offset,
                    "timestamp": turn.timestamp,
                },
            ))
            count += 1
        except Exception:
            pass
    if turns:
        _mirror_offsets[cache_key] = turns[-1].line_offset + 1
    return count
