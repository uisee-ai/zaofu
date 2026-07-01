"""StreamJsonTransport — Claude Code headless via the claude_code_sdk.

For each task dispatch:
  1. resolve the role's deterministic session_id from RoleSessionRegistry
  2. acquire a SessionLock on that id (mutex against concurrent --resume)
  3. drive claude_code_sdk.query() with prompt + ClaudeCodeOptions(resume=...)
  4. drain the async message stream into self._messages[role]
  5. release the lock

There is no long-lived process. spawn() and shutdown() are no-ops.
attach_handle() returns a `less +F .zf/logs/<role>.log` argv (no live attach).

This module imports claude_code_sdk lazily so importing the module in tests
does not require the SDK to be installed. The query function is dependency-
injected so tests can pass a fake without touching the real SDK.
"""

from __future__ import annotations

import asyncio
from enum import Enum
from pathlib import Path
from typing import Any, Awaitable, Callable

from zf.core.config.schema import RoleConfig
from zf.core.events.model import ZfEvent
from zf.core.state.role_sessions import RoleSessionRegistry
from zf.runtime.provider_stop import classify_provider_stop
from zf.runtime.session_mutex import SessionLock
from zf.runtime.spawn_coordinator import purge_stale_claude_session_lock
from zf.runtime.transport import AttachHandle, DispatchContext, TransportAdapter


QueryFn = Callable[..., Any]  # async generator factory


class DrainStatus(str, Enum):
    """Outcome of a single _drain pass.

    OK            — stream completed cleanly (or with rate_limit_event AFTER
                    assistant produced messages — B7 partial-progress case)
    RATE_LIMITED  — rate_limit_event hit before assistant said anything
    TIMEOUT       — exceeded transport_timeout_s without finishing
    """
    OK = "ok"
    RATE_LIMITED = "rate_limited"
    TIMEOUT = "timeout"


def _stringify(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(_stringify(c) for c in content)
    return str(content)


def _real_query() -> QueryFn:
    """Lazy import of claude_code_sdk.query so the module loads without the SDK."""
    from claude_code_sdk import query  # type: ignore
    return query


class StreamJsonTransport(TransportAdapter):
    def __init__(
        self,
        state_dir: Path,
        registry: RoleSessionRegistry,
        *,
        query_fn: QueryFn | None = None,
        cwd: Path | None = None,
        timeout_s: float = 120.0,
        max_turns: int = 30,
    ) -> None:
        self.state_dir = state_dir
        self.registry = registry
        # Legacy direct-construction fallback for tests; production
        # make_transport passes the resolved zf.yaml project_root as cwd.
        self.cwd = cwd or state_dir.parent
        self._query_fn = query_fn  # None → lazy import on first use
        self._timeout_s = timeout_s
        self._max_turns = max_turns
        self._messages: dict[str, list[Any]] = {}
        self._roles: dict[str, RoleConfig] = {}
        self._cwd_by_role: dict[str, Path] = {}
        self._pending_events: list[ZfEvent] = []
        self.lock_dir = state_dir / "locks" / "sessions"
        # G-XPORT-1: track the success/failure of the most recent send_task
        # per role. Set to True on successful drain, False on exception.
        # Unknown roles (never spawned) → not in dict → False.
        # Spawned but not yet queried → True (optimistic default).
        self._last_query_ok: dict[str, bool] = {}
        # G-XPORT-3: monotonic counter per role; bumped whenever new
        # messages drain in. capture_log() surfaces this as a heartbeat
        # line so Orchestrator's StuckDetector (which hashes capture_log
        # output) can distinguish "alive but quiet" from "stuck".
        self._heartbeat: dict[str, int] = {}
        self._session_used: set[str] = set()

    # -- TransportAdapter interface --

    def init(self, *, exclude_roles: set[str] | None = None) -> None:
        self.lock_dir.mkdir(parents=True, exist_ok=True)

    def is_session_running(self) -> bool:
        return False  # no long-lived session

    def spawn(
        self,
        role: RoleConfig,
        argv: list[str],
        *,
        cwd: Path | None = None,
    ) -> None:
        # No-op: stream-json has no long-lived process. We do remember the
        # role config so send_task can read its permission_mode etc.
        self.register_role(role, cwd=cwd)

    def register_role(self, role: RoleConfig, *, cwd: Path | None = None) -> None:
        """Record role config without spawning a long-lived process."""
        self._roles[role.instance_id] = role
        if cwd is not None:
            self._cwd_by_role[role.instance_id] = cwd
        # Preserve the legacy role.name lookup for single-instance roles.
        if role.instance_id == role.name:
            self._roles[role.name] = role
            if cwd is not None:
                self._cwd_by_role[role.name] = cwd
        self._messages.setdefault(role.instance_id, [])
        # G-XPORT-1: optimistic default — spawned role is alive until proven
        # otherwise by a failing send_task.
        self._last_query_ok.setdefault(role.instance_id, True)
        self._heartbeat.setdefault(role.instance_id, 0)

    def is_alive(self, role_name: str) -> bool:
        # G-XPORT-1: "alive" == most recent send_task succeeded (or never
        # ran, if the role was spawned). Unknown roles return False.
        return self._last_query_ok.get(role_name, False)

    def wait_ready(self, role_name: str, pattern: str, timeout: float) -> bool:
        return True  # nothing to wait for

    def send_task(
        self,
        role_name: str,
        briefing_path: Path,
        prompt: str,
        *,
        context: DispatchContext | None = None,
    ) -> None:
        session_id = str(self.registry.get_or_create(role_name))
        role = self._roles.get(role_name) or RoleConfig(name=role_name)
        role_cwd = self._cwd_for_role(role_name)
        is_resume = self._session_exists_on_disk(session_id, cwd=role_cwd)
        if not is_resume:
            # P0-1 (2026-06-19 e2e): the tmux SpawnCoordinator purges stale
            # ~/.claude.json lastSessionId / residual <uuid>.jsonl before
            # passing --session-id; this stream-json path did not, so a
            # re-dispatched worker reusing its deterministic session-id hit
            # "Session ID ... is already in use" and the drain raised — which
            # for a synth role meant the aggregate never produced its success
            # event (e.g. task_map.ready). Clear the same lock here before a
            # fresh-id launch. No-ops when a live process owns the uuid.
            purged = purge_stale_claude_session_lock(session_id)
            if any(purged.values()):
                self._pending_events.append(ZfEvent(
                    type="worker.spawn.stale_session_purged",
                    actor="zf-cli",
                    payload={
                        "instance_id": role_name,
                        "role": role.name,
                        "backend": role.backend,
                        "session_id": session_id,
                        "transport": "stream-json",
                        **purged,
                    },
                ))
        context = _complete_context(
            context,
            role=role,
            role_name=role_name,
            briefing_path=briefing_path,
        )
        def _drain_once(sid: str, resume: bool):
            with SessionLock(self.lock_dir, sid):
                return asyncio.run(
                    self._drain(
                        prompt=prompt, session_id=sid,
                        role=role, is_resume=resume,
                        cwd=role_cwd,
                    )
                )

        try:
            messages, status = _drain_once(session_id, is_resume)
        except Exception as exc:
            # P0-1 (2026-06-19 e2e): a role re-dispatched within a run (e.g. a
            # synthRole invoked a second time after its fanout children) reuses
            # its deterministic session-id; if the prior dispatch is still
            # tearing down, claude aborts with "Session ID is already in use"
            # and the fail-closed stale-lock purge cannot clear a live-held
            # lock. Rotate to a fresh id and retry once so the (synth) dispatch
            # still completes instead of timing the whole aggregate out.
            if not is_resume and "already in use" in str(exc).lower():
                session_id = str(self.registry.rotate(role_name))
                purge_stale_claude_session_lock(session_id)
                try:
                    messages, status = _drain_once(session_id, False)
                except Exception:
                    self._last_query_ok[role_name] = False
                    raise
            else:
                self._last_query_ok[role_name] = False
                raise

        self._session_used.add(role_name)
        self._messages.setdefault(role_name, []).extend(messages)
        if messages:
            self._bump_heartbeat(role_name)

        agent_events = self._messages_to_events(
            role_name, messages, context=context,
        )

        # B11: if drain hit rate_limit / timeout AND assistant never produced
        # any meaningful event (only SystemMessage init), surface an explicit
        # signal instead of silent fail. The orchestrator's cool-down handler
        # will pause Layer 2 dispatch until the cool-down expires.
        had_assistant_event = any(
            e.type in {"agent.thinking", "agent.text", "agent.tool.use",
                       "agent.tool.result", "agent.usage"}
            for e in agent_events
        )
        if status == DrainStatus.RATE_LIMITED and not had_assistant_event:
            agent_events.append(ZfEvent(
                type="agent.api_blocked",
                actor=role_name,
                task_id=context.task_id,
                correlation_id=context.trace_id,
                payload={
                    **context.to_payload(),
                    "reason": "rate_limit_event before assistant turn",
                    "provider_stop_reason": classify_provider_stop(
                        {"reason": "rate_limit_event before assistant turn"},
                        status=status.value,
                    ),
                    "session_id": session_id,
                },
            ))
            self._last_query_ok[role_name] = False
        elif status == DrainStatus.TIMEOUT:
            agent_events.append(ZfEvent(
                type="agent.timeout",
                actor=role_name,
                task_id=context.task_id,
                correlation_id=context.trace_id,
                payload={
                    **context.to_payload(),
                    "timeout_s": self._timeout_s,
                    "provider_stop_reason": classify_provider_stop(
                        {"reason": "timeout"},
                        status=status.value,
                    ),
                    "session_id": session_id,
                    "partial_messages": len(messages),
                },
            ))
            self._last_query_ok[role_name] = False
        else:
            self._last_query_ok[role_name] = True

        self._pending_events.extend(agent_events)

    def _session_exists_on_disk(self, session_id: str, *, cwd: Path | None = None) -> bool:
        option_cwd = cwd or self.cwd
        escaped = "-" + str(option_cwd).lstrip("/").replace("/", "-")
        session_file = Path.home() / ".claude" / "projects" / escaped / f"{session_id}.jsonl"
        return session_file.exists()

    def _cwd_for_role(self, role_name: str) -> Path:
        return self._cwd_by_role.get(role_name, self.cwd)

    def _bump_heartbeat(self, role_name: str) -> None:
        """G-XPORT-3: advance the role's heartbeat counter so capture_log
        output differs from its previous snapshot. Call this whenever new
        messages are drained into _messages."""
        self._heartbeat[role_name] = self._heartbeat.get(role_name, 0) + 1

    async def _drain(
        self,
        *,
        prompt: str,
        session_id: str,
        role: RoleConfig,
        is_resume: bool = False,
        cwd: Path | None = None,
    ) -> tuple[list[Any], DrainStatus]:
        query_fn = self._query_fn or _real_query()
        options = self._build_options(
            session_id,
            role,
            is_resume=is_resume,
            cwd=cwd or self.cwd,
        )
        collected: list[Any] = []
        status = DrainStatus.OK
        try:
            async with asyncio.timeout(self._timeout_s):
                async for msg in query_fn(prompt=prompt, options=options):
                    collected.append(msg)
        except asyncio.TimeoutError:
            status = DrainStatus.TIMEOUT
        except Exception as e:
            # Claude SDK raises MessageParseError on rate_limit_event — the
            # SDK doesn't know this stream-end marker. B7 used to swallow it
            # silently; B11 changed that to a status flag so send_task can
            # decide whether to surface a signal (RATE_LIMITED if no
            # assistant message was collected) or treat the partial drain
            # as success (if assistant did produce messages first).
            if "rate_limit_event" not in str(e):
                raise
            status = DrainStatus.RATE_LIMITED
        return collected, status

    def _build_options(
        self,
        session_id: str,
        role: RoleConfig,
        *,
        is_resume: bool = False,
        cwd: Path | None = None,
    ) -> Any:
        option_cwd = cwd or self.cwd
        try:
            from claude_code_sdk import ClaudeCodeOptions  # type: ignore
        except ImportError:
            class _Stub:
                pass
            stub = _Stub()
            sdk_perm = (
                "bypassPermissions"
                if role.permission_mode == "bypass"
                else "default"
            )
            stub.permission_mode = sdk_perm  # type: ignore[attr-defined]
            stub.allowed_tools = list(role.allowed_tools)  # type: ignore[attr-defined]
            stub.cwd = str(option_cwd)  # type: ignore[attr-defined]
            stub.model = (  # type: ignore[attr-defined]
                role.model if role.model and role.model != "placeholder" else None
            )
            stub.max_turns = self._max_turns  # type: ignore[attr-defined]
            stub.extra_args = {}  # type: ignore[attr-defined]
            if is_resume:
                stub.resume = session_id  # type: ignore[attr-defined]
            else:
                stub.session_id = session_id  # type: ignore[attr-defined]
                stub.extra_args = {"session-id": session_id}  # type: ignore[attr-defined]
            return stub

        sdk_perm = "bypassPermissions" if role.permission_mode == "bypass" else "default"
        opts: dict = dict(
            permission_mode=sdk_perm,
            allowed_tools=list(role.allowed_tools),
            cwd=str(option_cwd),
            model=role.model if role.model and role.model != "placeholder" else None,
            # B9: without max_turns, Claude CLI defaults to a low cap that
            # ends the stream right after the first tool call (observed:
            # Layer 2 reads briefing, gets tool_result, then stop with no
            # follow-up thinking). Default 30 is generous enough for one
            # wake to decompose a feature, set N contracts, and dispatch.
            max_turns=self._max_turns,
        )
        if is_resume:
            opts["resume"] = session_id
        else:
            opts["extra_args"] = {"session-id": session_id}
        return ClaudeCodeOptions(**opts)

    def capture_log(self, role_name: str, lines: int = 200) -> str:
        msgs = self._messages.get(role_name, [])
        # Render recent messages as text — extracts text blocks and tool names
        out: list[str] = []
        for m in msgs[-lines:]:
            content = getattr(m, "content", None)
            if content is None:
                out.append(repr(m))
                continue
            for block in content:
                text = getattr(block, "text", None)
                if text:
                    out.append(text)
                tool_name = getattr(block, "name", None)
                if tool_name and getattr(block, "input", None) is not None:
                    out.append(f"[tool_use: {tool_name}]")
        # G-XPORT-3: heartbeat line so the orchestrator's StuckDetector
        # (which hashes this output) can tell "alive but quiet" from
        # "stuck". Stable across reads when no new messages drain in.
        hb = self._heartbeat.get(role_name, 0)
        out.append(f"heartbeat: {hb}")
        return "\n".join(out)

    def poll_events(self) -> list[ZfEvent]:
        drained = self._pending_events
        self._pending_events = []
        return drained

    @staticmethod
    def _messages_to_events(
        role_name: str,
        messages: list[Any],
        *,
        context: DispatchContext | None = None,
    ) -> list[ZfEvent]:
        """Map claude_code_sdk messages to ZfEvent records.

        AssistantMessage(content=[TextBlock])     → agent.text
        AssistantMessage(content=[ToolUseBlock])  → agent.tool.use
        AssistantMessage(content=[ToolResultBlock])→ agent.tool.result
        AssistantMessage(content=[ThinkingBlock]) → agent.thinking
        ResultMessage                              → agent.usage
        Anything else                              → ignored
        """
        context = context or DispatchContext(instance_id=role_name)

        def _event(event_type: str, payload: dict[str, Any]) -> ZfEvent:
            return ZfEvent(
                type=event_type,
                actor=context.instance_id or role_name,
                task_id=context.task_id,
                correlation_id=context.trace_id,
                payload={**context.to_payload(), **payload},
            )

        out: list[ZfEvent] = []
        for m in messages:
            cls = type(m).__name__
            if cls == "ResultMessage" or all(
                hasattr(m, f) for f in ("session_id", "total_cost_usd", "usage")
            ):
                usage = getattr(m, "usage", {})
                payload = {
                    "session_id": getattr(m, "session_id", ""),
                    "total_cost_usd": getattr(m, "total_cost_usd", 0.0),
                    "usage": usage,
                    "num_turns": getattr(m, "num_turns", 0),
                    "duration_ms": getattr(m, "duration_ms", 0),
                    "is_error": getattr(m, "is_error", False),
                    "backend": context.backend or "claude-code",
                }
                context_ratio = _context_usage_ratio_from_usage(usage)
                if context_ratio is not None:
                    payload["context_usage_ratio"] = context_ratio
                    payload["ratio"] = context_ratio
                # B-1203-02: tag backend so consumers reading events.jsonl
                # can split cost/tokens by backend. stream-json is
                # Claude-only today (claude_code_sdk), so hardcoding is
                # correct; if a non-Claude SDK ever uses this path, we'll
                # plumb backend through _messages_to_events' call site.
                out.append(_event("agent.usage", payload))
                continue
            content = getattr(m, "content", None)
            if content is None:
                continue
            for block in content:
                if hasattr(block, "text") and not hasattr(block, "name"):
                    out.append(_event("agent.text", {"text": block.text}))
                elif hasattr(block, "name") and hasattr(block, "input"):
                    out.append(_event(
                        "agent.tool.use",
                        {
                            "tool": block.name,
                            "input": block.input,
                            "tool_use_id": getattr(block, "id", ""),
                        },
                    ))
                elif hasattr(block, "tool_use_id") and hasattr(block, "content"):
                    out.append(_event(
                        "agent.tool.result",
                        {
                            "tool_use_id": block.tool_use_id,
                            "content": _stringify(block.content),
                            "is_error": getattr(block, "is_error", False),
                        },
                    ))
                elif hasattr(block, "thinking"):
                    out.append(_event("agent.thinking", {"text": block.thinking}))
        return out

    def attach_handle(self, role_name: str | None) -> AttachHandle:
        if role_name:
            log = self.state_dir / "logs" / f"{role_name}.log"
        else:
            log = self.state_dir / "logs"
        return AttachHandle(
            argv=["less", "+F", str(log)],
            note=f"tailing {log} (no live attach for stream-json)",
        )

    def terminate(self, role_name: str) -> None:
        self._messages.pop(role_name, None)
        self._roles.pop(role_name, None)

    def shutdown(self, *, exclude_roles: set[str] | None = None) -> None:
        self._messages.clear()
        self._roles.clear()


def _complete_context(
    context: DispatchContext | None,
    *,
    role: RoleConfig,
    role_name: str,
    briefing_path: Path,
) -> DispatchContext:
    if context is None:
        return DispatchContext(
            role_name=role.name,
            instance_id=role.instance_id or role_name,
            backend=role.backend,
            briefing_path=briefing_path,
        )
    return DispatchContext(
        trace_id=context.trace_id,
        run_id=context.run_id,
        task_id=context.task_id,
        role_name=context.role_name or role.name,
        instance_id=context.instance_id or role.instance_id or role_name,
        backend=context.backend or role.backend,
        briefing_path=context.briefing_path or briefing_path,
        dispatch_id=context.dispatch_id,
    )


def _context_usage_ratio_from_usage(usage: Any) -> float | None:
    if not isinstance(usage, dict):
        try:
            usage = dict(vars(usage))
        except Exception:
            return None
    window = _intish(
        usage.get("model_context_window")
        or usage.get("context_window")
        or usage.get("window")
    )
    if window <= 0:
        return None
    effective = _intish(usage.get("effective_input_tokens"))
    if effective <= 0:
        effective = (
            _intish(usage.get("input_tokens"))
            + _intish(usage.get("cache_read_input_tokens"))
            + _intish(usage.get("cache_creation_input_tokens"))
        )
    if effective <= 0:
        return None
    return round(effective / window, 4)


def _intish(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0
