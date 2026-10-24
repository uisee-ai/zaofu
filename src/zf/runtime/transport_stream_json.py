"""StreamJsonTransport — Claude Code headless via the claude_code_sdk.

For each task dispatch:
  1. resolve the role's deterministic session_id from RoleSessionRegistry
  2. acquire a SessionLock on that id (mutex against concurrent --resume)
  3. drive claude_code_sdk.query() with prompt + ClaudeCodeOptions(resume=...)
  4. drain the async message stream into self._messages[role]
  5. release the lock

Production transports created by make_transport() serialize turns on one
background queue per role, so provider latency never blocks EventWatcher or
Orchestrator.run_once(). Direct construction remains synchronous by default
for focused tests and explicit one-shot callers.

There is no long-lived process. spawn() records the role-scoped launch
environment and shutdown() clears in-memory transport state.
attach_handle() returns a `less +F .zf/logs/<role>.log` argv (no live attach).

This module imports claude_code_sdk lazily so importing the module in tests
does not require the SDK to be installed. The query function is dependency-
injected so tests can pass a fake without touching the real SDK.
"""

from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass
from enum import Enum
import json
from pathlib import Path
import threading
import time
from typing import Any, Awaitable, Callable

from zf.core.config.schema import RoleConfig
from zf.core.events.model import ZfEvent
from zf.core.security.redaction import redact_text
from zf.core.state.role_sessions import RoleSessionRegistry
from zf.runtime.operations_metrics import OperationsMetricsRegistry
from zf.runtime.provider_telemetry import (
    ProviderTelemetryRuntime,
    TelemetryOperationContextV1,
)
from zf.runtime.provider_stop import classify_provider_stop
from zf.runtime.runtime_logs import write_runtime_log
from zf.runtime.stream_json_options import build_claude_stream_options
from zf.runtime.session_mutex import SessionLock
from zf.runtime.session_tailer import claude_session_path
from zf.runtime.spawn_coordinator import purge_stale_claude_session_lock
from zf.runtime.transport import AttachHandle, DispatchContext, TransportAdapter


QueryFn = Callable[..., Any]  # async generator factory
_MAX_QUEUED_TURNS_PER_ROLE = 1
_SESSION_FILE_SETTLE_TIMEOUT_S = 2.0
_SESSION_FILE_POLL_INTERVAL_S = 0.05


@dataclass(frozen=True)
class _QueuedDispatch:
    role_name: str
    briefing_path: Path
    prompt: str
    context: DispatchContext | None


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


def _latest_claude_api_error(cwd: Path, session_id: str) -> dict[str, object]:
    """Read one bounded provider API error from the current session ledger."""
    primary = claude_session_path(str(cwd), session_id)
    candidates = [primary]
    try:
        candidates.extend(sorted(
            primary.parent.glob(f"{primary.name}.archived-*"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        ))
    except OSError:
        pass
    for path in candidates:
        try:
            with path.open("rb") as handle:
                handle.seek(0, 2)
                size = handle.tell()
                handle.seek(max(0, size - 256_000))
                raw = handle.read().decode("utf-8", errors="replace")
        except OSError:
            continue
        for line in reversed(raw.splitlines()):
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(row, dict) or not row.get("isApiErrorMessage"):
                continue
            message = row.get("message")
            content = message.get("content") if isinstance(message, dict) else []
            text = "\n".join(
                str(item.get("text") or "")
                for item in content or []
                if isinstance(item, dict) and item.get("type") == "text"
            ).strip()
            return {
                "message": redact_text(text)[:500],
                "status": row.get("apiErrorStatus"),
                "kind": str(row.get("error") or "api_error")[:120],
                "transcript_ref": str(path),
            }
    return {}


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
        background_dispatch: bool = False,
        telemetry: ProviderTelemetryRuntime | None = None,
        operations_metrics: OperationsMetricsRegistry | None = None,
        runtime_logs_enabled: bool = True,
    ) -> None:
        self.state_dir = state_dir
        self.registry = registry
        # Legacy direct-construction fallback for tests; production
        # make_transport passes the resolved zf.yaml project_root as cwd.
        self.cwd = cwd or state_dir.parent
        self._query_fn = query_fn  # None → lazy import on first use
        self._timeout_s = timeout_s
        self._max_turns = max_turns
        self._background_dispatch = background_dispatch
        self._telemetry = telemetry
        self._operations_metrics = operations_metrics
        self._runtime_logs_enabled = runtime_logs_enabled
        self._messages: dict[str, list[Any]] = {}
        self._roles: dict[str, RoleConfig] = {}
        self._cwd_by_role: dict[str, Path] = {}
        self._env_by_role: dict[str, dict[str, str]] = {}
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
        self._dispatch_lock = threading.RLock()
        self._dispatch_queues: dict[str, deque[_QueuedDispatch]] = {}
        self._dispatch_threads: dict[str, threading.Thread] = {}
        from zf.runtime.stream_json_cancellation import StreamJsonTurnCancellation
        self._turn_cancellation = StreamJsonTurnCancellation()
        self._closing = False

    # -- TransportAdapter interface --

    def init(self, *, exclude_roles: set[str] | None = None) -> None:
        self.lock_dir.mkdir(parents=True, exist_ok=True)
        with self._dispatch_lock:
            self._closing = False

    def is_session_running(self) -> bool:
        return False  # no long-lived session

    def spawn(
        self,
        role: RoleConfig,
        argv: list[str],
        *,
        cwd: Path | None = None,
    ) -> None:
        self._turn_cancellation.reset(role.instance_id)
        # Stream-json has no long-lived process, but SpawnCoordinator's argv
        # carries role-scoped capability credentials. Preserve that leading
        # ``env KEY=VALUE ...`` block for the SDK subprocess.
        self.register_role(role, cwd=cwd)
        self._env_by_role[role.instance_id] = _leading_env_assignments(argv)
        self._last_query_ok[role.instance_id] = True

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
        # Registration only establishes routing. SpawnCoordinator.spawn()
        # performs launch preparation and marks the role ready for dispatch.
        self._last_query_ok.setdefault(role.instance_id, False)
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
        if not self._background_dispatch:
            self._send_task_sync(
                role_name,
                briefing_path,
                prompt,
                context=context,
            )
            return

        dispatch = _QueuedDispatch(
            role_name=role_name,
            briefing_path=briefing_path,
            prompt=prompt,
            context=context,
        )
        start_thread: threading.Thread | None = None
        with self._dispatch_lock:
            if self._closing:
                raise RuntimeError("stream-json transport is shutting down")
            queue = self._dispatch_queues.setdefault(role_name, deque())
            if len(queue) >= _MAX_QUEUED_TURNS_PER_ROLE:
                raise RuntimeError(
                    f"stream-json dispatch queue for {role_name!r} is full"
                )
            queue.append(dispatch)
            active = self._dispatch_threads.get(role_name)
            if active is None:
                start_thread = threading.Thread(
                    target=self._dispatch_loop,
                    args=(role_name,),
                    name=f"StreamJsonTransport-{role_name}",
                    daemon=True,
                )
                self._dispatch_threads[role_name] = start_thread
        if start_thread is not None:
            try:
                start_thread.start()
            except Exception:
                with self._dispatch_lock:
                    self._dispatch_threads.pop(role_name, None)
                    queued = self._dispatch_queues.get(role_name)
                    if queued:
                        try:
                            queued.remove(dispatch)
                        except ValueError:
                            pass
                raise

    def _dispatch_loop(self, role_name: str) -> None:
        while True:
            with self._dispatch_lock:
                queue = self._dispatch_queues.get(role_name)
                if self._closing or not queue:
                    self._dispatch_queues.pop(role_name, None)
                    self._dispatch_threads.pop(role_name, None)
                    return
                dispatch = queue.popleft()
            try:
                self._send_task_sync(
                    dispatch.role_name,
                    dispatch.briefing_path,
                    dispatch.prompt,
                    context=dispatch.context,
                )
            except Exception as exc:
                self._record_background_failure(dispatch, exc)

    def _record_background_failure(
        self,
        dispatch: _QueuedDispatch,
        exc: Exception,
    ) -> None:
        role = self._roles.get(dispatch.role_name) or RoleConfig(
            name=dispatch.role_name,
        )
        context = _complete_context(
            dispatch.context,
            role=role,
            role_name=dispatch.role_name,
            briefing_path=dispatch.briefing_path,
        )
        session_id = str(self.registry.get_or_create(dispatch.role_name))
        api_error = _latest_claude_api_error(
            self._cwd_for_role(dispatch.role_name),
            session_id,
        )
        generic_reason = redact_text(f"{type(exc).__name__}: {exc}")[:500]
        reason = str(api_error.get("message") or generic_reason)
        stop_reason = classify_provider_stop(
            {"reason": reason},
            status="transport_error",
        )
        self._queue_pending_events([ZfEvent(
            type="agent.api_blocked",
            actor=context.instance_id or dispatch.role_name,
            task_id=context.task_id,
            correlation_id=context.trace_id,
            payload={
                **context.to_payload(),
                "reason": reason,
                "provider_stop_reason": stop_reason,
                "provider_error_status": api_error.get("status"),
                "provider_error_kind": str(api_error.get("kind") or ""),
                "provider_transcript_ref": str(
                    api_error.get("transcript_ref") or ""
                ),
                "transport_error": generic_reason,
                "session_id": session_id,
            },
        )])

    def _send_task_sync(
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
        role_env = dict(
            self._env_by_role.get(role_name)
            or self._env_by_role.get(role.instance_id)
            or {}
        )
        is_resume = self._session_exists_on_disk(session_id, cwd=role_cwd)
        if not is_resume and role_name in self._session_used:
            is_resume = self._wait_for_session_file(
                session_id,
                cwd=role_cwd,
            )
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
                self._queue_pending_events([ZfEvent(
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
                )])
        context = _complete_context(
            context,
            role=role,
            role_name=role_name,
            briefing_path=briefing_path,
        )
        launch = None
        if self._telemetry is not None:
            launch = self._telemetry.launch(
                TelemetryOperationContextV1.from_dispatch(context),
                route="stream-json",
            )
            role_env.update(launch.env)
            self._record_telemetry_launch(context, launch)
        started_at = time.monotonic()
        def _drain_once(sid: str, resume: bool):
            with SessionLock(self.lock_dir, sid):
                return asyncio.run(
                    self._turn_cancellation.drain(role_name, lambda: self._drain(
                        prompt=prompt, session_id=sid,
                        role=role, is_resume=resume,
                        cwd=role_cwd,
                        env=role_env,
                    ))
                )

        try:
            messages, status = _drain_once(session_id, is_resume)
        except asyncio.CancelledError:
            return
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
                except asyncio.CancelledError:
                    return
                except Exception:
                    self._last_query_ok[role_name] = False
                    self._record_provider_operation(
                        context,
                        result="failed",
                        failure_class="provider_dispatch_error",
                        duration_s=time.monotonic() - started_at,
                    )
                    raise
            else:
                self._last_query_ok[role_name] = False
                self._record_provider_operation(
                    context,
                    result="failed",
                    failure_class="provider_dispatch_error",
                    duration_s=time.monotonic() - started_at,
                )
                raise

        self._session_used.add(role_name)
        self._messages.setdefault(role_name, []).extend(messages)
        if messages:
            self._bump_heartbeat(role_name)

        agent_events = self._messages_to_events(
            role_name,
            messages,
            context=context,
            model_context_window=role.context_window_tokens,
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

        self._record_provider_operation(
            context,
            result="completed" if self._last_query_ok[role_name] else "failed",
            failure_class=(
                "provider_rate_limited"
                if status == DrainStatus.RATE_LIMITED
                else "provider_timeout" if status == DrainStatus.TIMEOUT else ""
            ),
            duration_s=time.monotonic() - started_at,
        )

        self._queue_pending_events(agent_events)

    def _record_telemetry_launch(self, context: DispatchContext, launch: Any) -> None:
        capability = launch.capability
        payload = {
            "provider": capability.provider,
            "route": capability.route,
            "requested": capability.requested,
            "detected": capability.detected,
            "effective": capability.effective,
            "join_kind": capability.join_kind,
            "w3c_inbound": capability.w3c_inbound,
            "signals": capability.signals,
            "failure_class": capability.failure_class,
            "evidence_ref": "projection:provider_telemetry.json",
        }
        events = [ZfEvent(
            type="provider.telemetry.capability.observed",
            actor="zf-cli",
            task_id=context.task_id,
            correlation_id=context.trace_id,
            payload={**context.to_payload(), **payload},
        )]
        if capability.effective == "active":
            events.append(ZfEvent(
                type="provider.telemetry.context.bound",
                actor="zf-cli",
                task_id=context.task_id,
                correlation_id=context.trace_id,
                payload={
                    **context.to_payload(),
                    **payload,
                    "otel_trace_id": launch.context.otel_trace_id,
                    "otel_parent_span_id": launch.context.otel_parent_span_id,
                },
            ))
        self._queue_pending_events(events)

    def _record_provider_operation(
        self,
        context: DispatchContext,
        *,
        result: str,
        failure_class: str,
        duration_s: float,
    ) -> None:
        provider = str(context.backend or "claude")
        write_runtime_log(
            self.state_dir,
            level="ERROR" if result == "failed" else "INFO",
            component="stream-json",
            message="provider dispatch completed" if result == "completed" else "provider dispatch failed",
            failure_class=failure_class,
            fields={
                "zaofu_correlation_id": context.trace_id,
                "task_id": context.task_id,
                "workflow_run_id": context.run_id,
                "dispatch_id": context.dispatch_id,
                "attempt_id": context.attempt_id,
                "role_instance_id": context.instance_id,
                "provider": provider,
                "route": "stream-json",
                "operation_kind": "workflow_dispatch",
                "status": result,
            },
            enabled=self._runtime_logs_enabled,
        )
        if self._operations_metrics is not None:
            labels = {
                "provider": provider,
                "operation": "workflow_dispatch",
                "result": result,
            }
            if failure_class:
                labels["failure_class"] = failure_class
            self._operations_metrics.increment(
                "zf_provider_operations_total",
                labels=labels,
            )
            self._operations_metrics.observe(
                "zf_provider_operation_duration_seconds",
                duration_s,
                labels=labels,
            )

    def _session_exists_on_disk(self, session_id: str, *, cwd: Path | None = None) -> bool:
        option_cwd = cwd or self.cwd
        return claude_session_path(str(option_cwd), session_id).exists()

    def _wait_for_session_file(
        self,
        session_id: str,
        *,
        cwd: Path | None = None,
    ) -> bool:
        """Allow a completed SDK turn to finish persisting its resume file."""

        deadline = time.monotonic() + _SESSION_FILE_SETTLE_TIMEOUT_S
        while time.monotonic() < deadline:
            if self._session_exists_on_disk(session_id, cwd=cwd):
                return True
            time.sleep(_SESSION_FILE_POLL_INTERVAL_S)
        return self._session_exists_on_disk(session_id, cwd=cwd)

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
        env: dict[str, str] | None = None,
    ) -> tuple[list[Any], DrainStatus]:
        query_fn = self._query_fn or _real_query()
        options = self._build_options(
            session_id,
            role,
            is_resume=is_resume,
            cwd=cwd or self.cwd,
            env=env,
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
        self, session_id: str, role: RoleConfig, *, is_resume: bool = False,
        cwd: Path | None = None, env: dict[str, str] | None = None,
    ) -> Any:
        return build_claude_stream_options(
            session_id, role, cwd=cwd or self.cwd, max_turns=self._max_turns,
            is_resume=is_resume, env=env,
        )

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
        with self._dispatch_lock:
            drained = self._pending_events
            self._pending_events = []
            return drained

    def _queue_pending_events(self, events: list[ZfEvent]) -> None:
        if not events:
            return
        with self._dispatch_lock:
            self._pending_events.extend(events)

    @staticmethod
    def _messages_to_events(
        role_name: str,
        messages: list[Any],
        *,
        context: DispatchContext | None = None,
        model_context_window: int = 0,
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
        observed_model = ""
        for m in messages:
            cls = type(m).__name__
            message_model = str(getattr(m, "model", "") or "")
            if message_model:
                observed_model = message_model
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
                if observed_model:
                    payload["model"] = observed_model
                if model_context_window > 0:
                    payload["model_context_window"] = model_context_window
                context_ratio = _context_usage_ratio_from_usage(usage)
                if context_ratio is None and model_context_window > 0:
                    usage_mapping = (
                        dict(usage)
                        if isinstance(usage, dict)
                        else dict(vars(usage))
                    )
                    usage_with_window = {
                        **usage_mapping,
                        "model_context_window": model_context_window,
                    }
                    context_ratio = _context_usage_ratio_from_usage(
                        usage_with_window
                    )
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
        with self._dispatch_lock:
            self._dispatch_queues.pop(role_name, None)
            thread = self._dispatch_threads.get(role_name)
        self._turn_cancellation.cancel_and_join(role_name, thread)
        with self._dispatch_lock:
            self._dispatch_queues.pop(role_name, None)
            self._messages.pop(role_name, None)
            self._roles.pop(role_name, None)
            self._cwd_by_role.pop(role_name, None)
            self._env_by_role.pop(role_name, None)
            self._last_query_ok.pop(role_name, None)
            self._heartbeat.pop(role_name, None)
            self._session_used.discard(role_name)

    def shutdown(self, *, exclude_roles: set[str] | None = None) -> None:
        with self._dispatch_lock:
            self._closing = True
            roles = set(self._roles) | set(self._dispatch_threads)
            self._dispatch_queues.clear()
        for role_name in roles - (exclude_roles or set()):
            self.terminate(role_name)
        with self._dispatch_lock:
            self._messages.clear()
            self._roles.clear()
            self._cwd_by_role.clear()
            self._env_by_role.clear()
            self._last_query_ok.clear()
            self._heartbeat.clear()
            self._session_used.clear()


def _leading_env_assignments(argv: list[str]) -> dict[str, str]:
    """Extract SpawnCoordinator's leading ``env KEY=VALUE ...`` block."""
    if not argv or argv[0] != "env":
        return {}
    assignments: dict[str, str] = {}
    for item in argv[1:]:
        if "=" not in item:
            break
        name, value = item.split("=", 1)
        if not name:
            break
        assignments[name] = value
    return assignments


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
        parent_task_id=context.parent_task_id,
        role_name=context.role_name or role.name,
        instance_id=context.instance_id or role.instance_id or role_name,
        backend=context.backend or role.backend,
        briefing_path=context.briefing_path or briefing_path,
        dispatch_id=context.dispatch_id,
        operation_id=context.operation_id,
        attempt_id=context.attempt_id,
        lease_id=context.lease_id,
        task_pipeline_stage=context.task_pipeline_stage,
        operation_generation=context.operation_generation,
        task_map_generation=context.task_map_generation,
        workspace_generation=context.workspace_generation,
        placement_epoch=context.placement_epoch,
        task_stage_session_binding=context.task_stage_session_binding,
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
