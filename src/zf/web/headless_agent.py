"""Headless Kanban Agent backends for the Web dashboard.

This module owns interaction state only. It may persist provider thread/session
ids so a later chat turn can resume context, but it never mutates kanban truth.
"""

from __future__ import annotations

import json
import logging
import os
import queue
import shlex
import shutil
import subprocess
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator, Protocol

from zf import __version__
from zf.core.state.atomic_io import atomic_write_text
from zf.core.state.locks import FileLock
from zf.runtime.operations_metrics import OperationsMetricsRegistry
from zf.runtime.provider_telemetry import (
    ProviderTelemetryRuntime,
    TelemetryOperationContextV1,
)
from zf.runtime.runtime_logs import write_runtime_log
from zf.runtime.channel_contracts import (
    normalize_permission_profile,
    permission_profile_write_policy,
)
from zf.runtime.cli_command import (
    ZF_CLI_CMD_ENV,
    default_zf_cli_cmd,
)
from zf.runtime.provider_permissions import (
    build_provider_permission_snapshot,
    claude_permission_mode_for_profile,
    codex_security_config_for_profile,
    provider_permission_drift,
    snapshot_with_provider_session,
)
from zf.runtime.task_workflow_plans import TASK_WORKFLOW_PARAMETER_KEYS
from zf.web.agent_session_runtime import (
    agent_session_process,
    agent_session_run_cancelled,
    run_key,
)

logger = logging.getLogger(__name__)


SessionCallback = Callable[[str], None]

DEFAULT_KANBAN_AGENT_HEADLESS_IDLE_TIMEOUT_S = 30 * 60.0
DEFAULT_CODEX_HEADLESS_TOOL_TIMEOUT_S = 2 * 60 * 60.0
TASK_WORKFLOW_PARAMETER_KEYS_TEXT = ", ".join(
    sorted(TASK_WORKFLOW_PARAMETER_KEYS)
)


def _headless_subprocess_env(extra_env: dict[str, str] | None = None) -> dict[str, str]:
    env = dict(os.environ)
    for key in tuple(env):
        if key.startswith("ZF_") and any(
            marker in key
            for marker in ("TOKEN", "PASSCODE", "SECRET", "ENCRYPT_KEY")
        ):
            env.pop(key, None)
    env[ZF_CLI_CMD_ENV] = _headless_zf_cli_cmd()
    env.update(extra_env or {})
    return env


def _headless_zf_cli_cmd() -> str:
    return os.environ.get(ZF_CLI_CMD_ENV, "").strip() or default_zf_cli_cmd()


@dataclass(frozen=True)
class HeadlessMessage:
    type: str
    content: str = ""
    session_id: str = ""
    tool: str = ""
    input: Any = None
    output: str = ""
    raw: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "content": self.content,
            "session_id": self.session_id,
            "tool": self.tool,
            "input": self.input,
            "output": self.output,
            "raw": self.raw,
        }


MessageCallback = Callable[[HeadlessMessage], None]


@dataclass(frozen=True)
class HeadlessTurnResult:
    ok: bool
    status: str
    backend: str
    thread_id: str
    provider_session_id: str
    reply: str
    messages: list[HeadlessMessage]
    usage: dict[str, Any]
    resumed: bool = False
    fallback_reason: str = ""
    error: str = ""
    permission_snapshot: dict[str, Any] = field(default_factory=dict)
    permission_drift: dict[str, Any] = field(default_factory=dict)
    incomplete: bool = False
    completion_reason: str = ""
    continuation_count: int = 0
    telemetry: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "status": self.status,
            "backend": self.backend,
            "thread_id": self.thread_id,
            "provider_session_id": self.provider_session_id,
            "reply": self.reply,
            "messages": [message.to_dict() for message in self.messages],
            "usage": self.usage,
            "resumed": self.resumed,
            "fallback_reason": self.fallback_reason,
            "error": self.error,
            "permission_snapshot": self.permission_snapshot,
            "permission_drift": self.permission_drift,
            "incomplete": self.incomplete,
            "completion_reason": self.completion_reason,
            "continuation_count": self.continuation_count,
            "telemetry": self.telemetry,
        }


class HeadlessBackend(Protocol):
    backend_id: str

    def available(self) -> bool:
        ...

    def run_turn(
        self,
        *,
        prompt: str,
        cwd: Path,
        system_prompt: str,
        thread_id: str,
        provider_session_id: str,
        on_session_id: SessionCallback,
        on_message: MessageCallback | None,
        timeout_s: float,
        thinking_level: str = "",
        run_id: str = "",
        run_thread_id: str = "",
        project_id: str = "",
        conversation_id: str = "",
        permission_profile: str = "read_only",
    ) -> HeadlessTurnResult:
        ...


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _safe_scope(value: str) -> str:
    return value if value in {"project", "task"} else "project"


class HeadlessThreadStore:
    """Durable local interaction state for hidden Kanban Agent threads."""

    def __init__(self, *, state_dir: Path, project_root: Path) -> None:
        self.state_dir = Path(state_dir)
        self.project_root = Path(project_root)
        self.thread_dir = self.state_dir / "operator" / "threads"

    def thread_id(
        self,
        *,
        scope: str = "project",
        task_id: str = "",
        thread_key: str = "",
    ) -> str:
        stable = f"zaofu-kanban-agent:{self.project_root.resolve()}:{_safe_scope(scope)}:{task_id or ''}"
        if thread_key:
            stable = f"{stable}:{thread_key}"
        return str(uuid.uuid5(uuid.NAMESPACE_URL, stable))

    def path_for(self, thread_id: str) -> Path:
        return self.thread_dir / f"{thread_id}.json"

    @contextmanager
    def locked(self, thread_id: str) -> Iterator[None]:
        with FileLock(self.thread_dir / f"{thread_id}.lock"):
            yield

    def load(
        self,
        *,
        scope: str = "project",
        task_id: str = "",
        thread_key: str = "",
    ) -> dict[str, Any]:
        scope = _safe_scope(scope)
        thread_id = self.thread_id(scope=scope, task_id=task_id, thread_key=thread_key)
        path = self.path_for(thread_id)
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    data.setdefault("thread_id", thread_id)
                    data.setdefault("scope", scope)
                    data.setdefault("task_id", task_id)
                    data.setdefault("thread_key", thread_key)
                    data.setdefault("providers", {})
                    data.setdefault("messages", [])
                    return data
            except json.JSONDecodeError:
                pass
        return {
            "thread_id": thread_id,
            "scope": scope,
            "task_id": task_id,
            "thread_key": thread_key,
            "status": "idle",
            "providers": {},
            "messages": [],
            "last_reply": "",
            "created_at": _now(),
            "updated_at": _now(),
        }

    def save(self, thread: dict[str, Any]) -> None:
        thread = dict(thread)
        messages = list(thread.get("messages", []) or [])
        thread["messages"] = messages[-200:]
        thread["updated_at"] = _now()
        atomic_write_text(
            self.path_for(str(thread["thread_id"])),
            json.dumps(thread, ensure_ascii=False, indent=2) + "\n",
        )

    def provider_session_id(self, thread: dict[str, Any], *, backend: str) -> str:
        provider = self._provider(thread, backend)
        return str(provider.get("provider_session_id") or "")

    def pin_provider_session(
        self,
        thread: dict[str, Any],
        *,
        backend: str,
        provider_session_id: str,
        workdir: str,
        status: str,
        permission_snapshot: dict[str, Any] | None = None,
        permission_drift: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not provider_session_id:
            return thread
        provider = self._provider(thread, backend)
        provider.update({
            "provider_session_id": provider_session_id,
            "workdir": workdir,
            "status": status,
            "updated_at": _now(),
        })
        if permission_snapshot:
            provider["permission_snapshot"] = permission_snapshot
        if permission_drift:
            provider["permission_drift"] = permission_drift
        thread["status"] = status
        self.save(thread)
        return thread

    def provider_permission_snapshot(self, thread: dict[str, Any], *, backend: str) -> dict[str, Any]:
        provider = self._provider(thread, backend)
        snapshot = provider.get("permission_snapshot")
        return dict(snapshot) if isinstance(snapshot, dict) else {}

    def record_turn(
        self,
        thread: dict[str, Any],
        *,
        result: HeadlessTurnResult,
        workdir: str,
    ) -> dict[str, Any]:
        provider = self._provider(thread, result.backend)
        if result.provider_session_id:
            provider["provider_session_id"] = result.provider_session_id
            provider["workdir"] = workdir
        provider["status"] = result.status
        provider["updated_at"] = _now()
        if result.fallback_reason:
            provider["fallback_reason"] = result.fallback_reason
        if result.error:
            provider["error"] = result.error
        if result.permission_snapshot:
            provider["permission_snapshot"] = result.permission_snapshot
        if result.permission_drift:
            provider["permission_drift"] = result.permission_drift
        thread["status"] = result.status
        thread["last_backend"] = result.backend
        thread["last_reply"] = result.reply
        thread["last_error"] = result.error
        thread["last_fallback_reason"] = result.fallback_reason
        thread.setdefault("messages", []).extend([
            {
                **message.to_dict(),
                "backend": result.backend,
                "ts": _now(),
            }
            for message in result.messages
        ])
        self.save(thread)
        return thread

    @staticmethod
    def _provider(thread: dict[str, Any], backend: str) -> dict[str, Any]:
        providers = thread.setdefault("providers", {})
        provider = providers.setdefault(backend, {})
        return provider


class ClaudeHeadlessBackend:
    backend_id = "claude-headless"

    def __init__(self, *, command: str | None = None, max_turns: int | None = None) -> None:
        self.command = command or os.environ.get(
            "ZF_KANBAN_AGENT_CLAUDE_HEADLESS_CMD",
            os.environ.get("ZF_KANBAN_AGENT_CLAUDE_CMD", "claude"),
        )
        # 500: read-heavy / long agentic channel+kanban conversations (read a
        # multi-KB spec via many tool calls, multi-round refine, then emit a
        # proposal) blew past the old cap of 8 and returned status:failed
        # (channel-kanban E2E 2026-07-09). 500 is a generous ceiling that still
        # acts as a runaway circuit-breaker (a tool-call loop cannot burn tokens
        # unbounded). Overridable via env; do NOT remove the cap entirely.
        if max_turns is None:
            max_turns = int(os.environ.get("ZF_KANBAN_AGENT_CLAUDE_MAX_TURNS", "500"))
        self.max_turns = max_turns
        self._telemetry_env: dict[str, str] = {}

    def set_telemetry_env(self, env: dict[str, str]) -> None:
        self._telemetry_env = dict(env)

    def available(self) -> bool:
        parts = shlex.split(self.command)
        return bool(parts and shutil.which(parts[0]))

    def build_args(
        self,
        *,
        thread_id: str,
        provider_session_id: str = "",
        system_prompt: str = "",
        thinking_level: str = "",
        permission_profile: str = "read_only",
    ) -> list[str]:
        args = shlex.split(self.command)
        permission_mode = os.environ.get(
            "ZF_KANBAN_AGENT_CLAUDE_HEADLESS_PERMISSION_MODE",
            _claude_permission_mode_for_profile(permission_profile),
        )
        args.extend([
            "-p",
            "--output-format",
            "stream-json",
            "--input-format",
            "stream-json",
            "--verbose",
            "--permission-mode",
            permission_mode,
            "--max-turns",
            str(self.max_turns),
        ])
        # B-STREAM-01: opt into Anthropic partial-message frames so streaming is
        # token-level (parity with Codex's app-server deltas) instead of
        # block-level. Requires the stream-json/--verbose pair above. Env-gated
        # (default on) so a CLI without the flag can fall back to block-level by
        # setting the env to "0".
        if _truthy_value(
            os.environ.get("ZF_KANBAN_AGENT_CLAUDE_HEADLESS_PARTIAL_MESSAGES", "1")
        ):
            args.append("--include-partial-messages")
        # Only restrict tools when an explicit non-empty list is configured.
        # An unset env → "" must NOT become `--tools ""` (an EMPTY allowlist that
        # disables every tool, so claude can't Read/Bash and describes tools as
        # text instead — verified 2026-06-22). Empty / "default" → no flag → all
        # tools, parity with codex.
        tools = os.environ.get("ZF_KANBAN_AGENT_CLAUDE_HEADLESS_TOOLS", "").strip()
        if tools and tools != "default":
            args.extend(["--tools", tools])
        if system_prompt:
            args.extend(["--append-system-prompt", system_prompt])
        model = os.environ.get("ZF_KANBAN_AGENT_CLAUDE_HEADLESS_MODEL", "").strip()
        if model:
            args.extend(["--model", model])
        if thinking_level:
            args.extend(["--effort", thinking_level])
        if provider_session_id:
            args.extend(["--resume", provider_session_id])
        else:
            args.extend(["--session-id", thread_id])
        return args

    def run_turn(
        self,
        *,
        prompt: str,
        cwd: Path,
        system_prompt: str,
        thread_id: str,
        provider_session_id: str,
        on_session_id: SessionCallback,
        on_message: MessageCallback | None,
        timeout_s: float,
        thinking_level: str = "",
        run_id: str = "",
        run_thread_id: str = "",
        project_id: str = "",
        conversation_id: str = "",
        permission_profile: str = "read_only",
    ) -> HeadlessTurnResult:
        args = self.build_args(
            thread_id=thread_id,
            provider_session_id=provider_session_id,
            system_prompt=system_prompt,
            thinking_level=thinking_level,
            permission_profile=permission_profile,
        )
        started_with_resume = bool(provider_session_id)
        try:
            process = subprocess.Popen(
                args,
                cwd=str(cwd),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env=_headless_subprocess_env(self._telemetry_env),
            )
            session_key = run_key(
                run_id=run_id,
                thread_id=run_thread_id or thread_id,
                project_id=project_id,
                conversation_id=conversation_id,
            )
            with agent_session_process(session_key, provider=self.backend_id, process=process) as cancel_requested:
                if cancel_requested:
                    parsed = _ParsedClaudeStream("", "", [], {})
                    stderr = ""
                    timed_out = False
                else:
                    parsed, stderr, timed_out = _stream_claude_process(
                        process,
                        prompt=prompt,
                        timeout_s=timeout_s,
                        on_session_id=on_session_id,
                        on_message=on_message,
                    )
        except OSError as exc:
            if agent_session_run_cancelled(run_key(run_id=run_id, thread_id=run_thread_id or thread_id)):
                return _cancelled_result(self.backend_id, thread_id, resumed=started_with_resume)
            return HeadlessTurnResult(
                ok=False,
                status="failed",
                backend=self.backend_id,
                thread_id=thread_id,
                provider_session_id="",
                reply="",
                messages=[],
                usage={},
                resumed=started_with_resume,
                error=str(exc),
            )
        if agent_session_run_cancelled(run_key(run_id=run_id, thread_id=run_thread_id or thread_id)):
            return _cancelled_result(
                self.backend_id,
                thread_id,
                provider_session_id=parsed.provider_session_id,
                messages=parsed.messages,
                usage=parsed.usage,
                resumed=started_with_resume,
            )
        if timed_out:
            return HeadlessTurnResult(
                ok=False,
                status="timeout",
                backend=self.backend_id,
                thread_id=thread_id,
                provider_session_id=parsed.provider_session_id,
                reply=parsed.reply,
                messages=parsed.messages,
                usage=parsed.usage,
                resumed=started_with_resume,
                error=f"claude headless timed out after {timeout_s:.0f}s",
            )
        error = _tail(stderr)
        ok = (
            process.returncode == 0
            and not parsed.is_error
            and not parsed.incomplete
        )
        status = "completed" if ok else (
            "incomplete" if parsed.incomplete else "failed"
        )
        return HeadlessTurnResult(
            ok=ok,
            status=status,
            backend=self.backend_id,
            thread_id=thread_id,
            provider_session_id=parsed.provider_session_id,
            reply=parsed.reply,
            messages=parsed.messages,
            usage=parsed.usage,
            resumed=started_with_resume,
            error="" if ok else (
                f"provider response incomplete: {parsed.completion_reason}"
                if parsed.incomplete
                else parsed.error or error or f"claude exited {process.returncode}"
            ),
            incomplete=parsed.incomplete,
            completion_reason=parsed.completion_reason,
        )


def _claude_permission_mode_for_profile(permission_profile: str) -> str:
    return claude_permission_mode_for_profile(permission_profile)


def _headless_failure_class(status: str) -> str:
    normalized = str(status or "failed").strip().lower()
    if normalized == "timeout":
        return "provider_timeout"
    if normalized == "cancelled":
        return "provider_cancelled"
    if normalized == "sandbox_unsupported":
        return "sandbox_unsupported"
    if normalized == "permission_drift_blocked":
        return "permission_drift"
    return "provider_turn_failed"


def _codex_security_config(permission_profile: str) -> dict[str, str]:
    return codex_security_config_for_profile(permission_profile)


def _codex_sandbox_preflight_error(args: list[str], security: dict[str, str]) -> str:
    sandbox = str(security.get("sandbox") or "")
    if sandbox == "danger-full-access":
        return ""
    if _truthy_value(os.environ.get("ZF_KANBAN_AGENT_CODEX_HEADLESS_SKIP_SANDBOX_PREFLIGHT", "")):
        return ""
    if not args or Path(args[0]).name != "codex":
        return ""
    unshare = shutil.which("unshare")
    if not unshare:
        return ""
    try:
        probe = subprocess.run(
            [unshare, "-n", "true"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=5,
            check=False,
        )
    except Exception:
        return ""
    text = f"{probe.stdout}\n{probe.stderr}"
    if probe.returncode == 0 or "operation not permitted" not in text.lower():
        return ""
    detail = _tail(text, limit=600)
    return _codex_sandbox_error_with_hint(
        f"Codex sandbox unsupported for sandbox={sandbox}: {detail}"
    )


def _is_codex_sandbox_unsupported_error(error: str) -> bool:
    text = error.lower()
    return (
        "bwrap: loopback" in text
        or "failed rtm_newaddr" in text
        or ("unshare" in text and "operation not permitted" in text)
    )


def _is_codex_timeout_error(error: str) -> bool:
    text = error.lower()
    return "codex turn timed out" in text or (
        "codex request " in text and " timed out" in text
    )


def _codex_failure_status(error: str) -> str:
    if _is_codex_sandbox_unsupported_error(error):
        return "sandbox_unsupported"
    if _is_codex_timeout_error(error):
        return "timeout"
    return "failed"


def _codex_sandbox_error_with_hint(error: str) -> str:
    hint = (
        "Fix host namespace/bubblewrap support, or for an explicitly trusted local "
        "smoke set ZF_KANBAN_AGENT_CODEX_HEADLESS_SANDBOX=danger-full-access and restart webkanban."
    )
    if hint in error:
        return error
    return f"{error}\n{hint}"


_CODEX_NONFATAL_STDERR_MARKERS = (
    "Codex could not find bubblewrap on PATH",
    "Codex will use the bundled bubblewrap",
)


def _codex_stderr_tail(stderr_parts: list[str], *, limit: int = 4000) -> str:
    lines: list[str] = []
    for raw in stderr_parts:
        line = str(raw or "").strip()
        if not line:
            continue
        if any(marker in line for marker in _CODEX_NONFATAL_STDERR_MARKERS):
            continue
        lines.append(line)
    return _tail("\n".join(lines), limit=limit)


def _codex_timeout_error(
    kind: str,
    *,
    timeout_s: float,
    method: str = "",
    stderr: str = "",
    idle: bool = False,
) -> str:
    target = f" {method}" if method else ""
    if idle:
        detail = f" after {timeout_s:g}s without Codex app-server events"
    else:
        detail = f" after {timeout_s:g}s waiting for Codex app-server"
    suffix = f": {stderr}" if stderr else ""
    return f"codex {kind}{target} timed out{detail}{suffix}"


def _codex_reasoning_config(thinking_level: str) -> dict[str, Any]:
    if not thinking_level:
        return {}
    return {"config": {"model_reasoning_effort": thinking_level}}


class CodexHeadlessBackend:
    backend_id = "codex-headless"

    def __init__(
        self,
        *,
        command: str | None = None,
        tool_timeout_s: float | None = None,
    ) -> None:
        self.command = command or os.environ.get(
            "ZF_KANBAN_AGENT_CODEX_HEADLESS_CMD",
            os.environ.get("ZF_KANBAN_AGENT_CODEX_CMD", "codex"),
        )
        if tool_timeout_s is None:
            raw_tool_timeout = os.environ.get(
                "ZF_CODEX_HEADLESS_TOOL_TIMEOUT_S",
                str(DEFAULT_CODEX_HEADLESS_TOOL_TIMEOUT_S),
            )
            try:
                tool_timeout_s = float(raw_tool_timeout)
            except (TypeError, ValueError):
                tool_timeout_s = DEFAULT_CODEX_HEADLESS_TOOL_TIMEOUT_S
        self.tool_timeout_s = (
            tool_timeout_s
            if tool_timeout_s > 0
            else DEFAULT_CODEX_HEADLESS_TOOL_TIMEOUT_S
        )
        self._telemetry_env: dict[str, str] = {}

    def set_telemetry_env(self, env: dict[str, str]) -> None:
        self._telemetry_env = dict(env)

    def available(self) -> bool:
        parts = shlex.split(self.command)
        return bool(parts and shutil.which(parts[0]))

    def build_args(self) -> list[str]:
        args = shlex.split(self.command)
        if "app-server" not in args:
            args.extend(["app-server"])
        if "--listen" not in args:
            args.extend(["--listen", "stdio://"])
        return args

    def run_turn(
        self,
        *,
        prompt: str,
        cwd: Path,
        system_prompt: str,
        thread_id: str,
        provider_session_id: str,
        on_session_id: SessionCallback,
        on_message: MessageCallback | None,
        timeout_s: float,
        thinking_level: str = "",
        run_id: str = "",
        run_thread_id: str = "",
        project_id: str = "",
        conversation_id: str = "",
        permission_profile: str = "read_only",
    ) -> HeadlessTurnResult:
        args = self.build_args()
        security_config = _codex_security_config(permission_profile)
        preflight_error = _codex_sandbox_preflight_error(args, security_config)
        if preflight_error:
            return HeadlessTurnResult(
                ok=False,
                status="sandbox_unsupported",
                backend=self.backend_id,
                thread_id=thread_id,
                provider_session_id=provider_session_id,
                reply="",
                messages=[],
                usage={},
                resumed=bool(provider_session_id),
                error=preflight_error,
            )
        client = _CodexRpcClient(
            args,
            cwd=cwd,
            timeout_s=timeout_s,
            tool_timeout_s=self.tool_timeout_s,
            permission_profile=permission_profile,
            env=_headless_subprocess_env(self._telemetry_env),
        )
        resumed = bool(provider_session_id)
        fallback_reason = ""
        session_key = run_key(
            run_id=run_id,
            thread_id=run_thread_id or thread_id,
            project_id=project_id,
            conversation_id=conversation_id,
        )
        try:
            client.start()
            if client.process is None:
                raise RuntimeError("codex app-server failed to start")
            with agent_session_process(session_key, provider=self.backend_id, process=client.process) as cancel_requested:
                if cancel_requested:
                    return _cancelled_result(self.backend_id, thread_id, resumed=resumed)
                client.request("initialize", {
                    "clientInfo": {
                        "name": "zaofu-kanban-agent",
                        "title": "ZaoFu Kanban Agent",
                        "version": __version__,
                    },
                    "capabilities": {"experimentalApi": True},
                })
                client.notify("initialized")
                codex_thread_id = ""
                if provider_session_id:
                    try:
                        result = client.request("thread/resume", {
                            "threadId": provider_session_id,
                            "cwd": str(cwd),
                            "developerInstructions": system_prompt or None,
                            "persistExtendedHistory": True,
                            **security_config,
                            **_codex_reasoning_config(thinking_level),
                        })
                        codex_thread_id = _extract_thread_id(result)
                    except RuntimeError as exc:
                        fallback_reason = f"resume failed; retried fresh: {exc}"
                if not codex_thread_id:
                    result = client.request("thread/start", {
                        "cwd": str(cwd),
                        "developerInstructions": system_prompt or None,
                        "experimentalRawEvents": False,
                        "persistExtendedHistory": True,
                        **security_config,
                        **_codex_reasoning_config(thinking_level),
                    })
                    codex_thread_id = _extract_thread_id(result)
                    resumed = False
                if not codex_thread_id:
                    raise RuntimeError("codex app-server returned no thread id")
                client.thread_id = codex_thread_id
                on_session_id(codex_thread_id)
                turn_params: dict[str, Any] = {
                    "threadId": codex_thread_id,
                    "input": [{"type": "text", "text": prompt}],
                }
                if thinking_level:
                    turn_params["effort"] = thinking_level
                client.request("turn/start", turn_params)
                (
                    reply,
                    usage,
                    incomplete,
                    completion_reason,
                ) = client.wait_turn(on_message=on_message)
                if agent_session_run_cancelled(session_key):
                    return _cancelled_result(
                        self.backend_id,
                        thread_id,
                        provider_session_id=codex_thread_id,
                        usage=usage,
                        resumed=resumed,
                    )
                return HeadlessTurnResult(
                    ok=not incomplete,
                    status="incomplete" if incomplete else "completed",
                    backend=self.backend_id,
                    thread_id=thread_id,
                    provider_session_id=codex_thread_id,
                    reply=reply,
                    messages=[HeadlessMessage(type="text", content=reply)] if reply else [],
                    usage=usage,
                    resumed=resumed,
                    fallback_reason=fallback_reason,
                    error=(
                        f"provider response incomplete: {completion_reason}"
                        if incomplete
                        else ""
                    ),
                    incomplete=incomplete,
                    completion_reason=completion_reason,
                )
        except Exception as exc:
            if agent_session_run_cancelled(session_key):
                return _cancelled_result(self.backend_id, thread_id, resumed=resumed)
            error = str(exc)
            status = _codex_failure_status(error)
            incomplete = _provider_completion_incomplete(status, error)
            return HeadlessTurnResult(
                ok=False,
                status="incomplete" if incomplete else status,
                backend=self.backend_id,
                thread_id=thread_id,
                provider_session_id=client.thread_id or provider_session_id,
                reply=(
                    client._completed_reply
                    or "".join(client._reply_parts)
                ).strip(),
                messages=[],
                usage=dict(client._usage),
                resumed=resumed,
                fallback_reason=fallback_reason,
                error=_codex_sandbox_error_with_hint(error) if status == "sandbox_unsupported" else error,
                incomplete=incomplete,
                completion_reason=error if incomplete else "",
            )
        finally:
            client.close()


class KanbanHeadlessAgent:
    """Run hidden-thread Kanban Agent turns through a headless backend."""

    def __init__(
        self,
        *,
        state_dir: Path,
        project_root: Path,
        backends: dict[str, HeadlessBackend] | None = None,
        timeout_s: float | None = None,
        telemetry: ProviderTelemetryRuntime | None = None,
        operations_metrics: OperationsMetricsRegistry | None = None,
        runtime_logs_enabled: bool = True,
    ) -> None:
        self.state_dir = Path(state_dir)
        self.project_root = Path(project_root)
        self.store = HeadlessThreadStore(
            state_dir=self.state_dir,
            project_root=self.project_root,
        )
        self.backends = backends or {
            "claude-headless": ClaudeHeadlessBackend(),
            "codex-headless": CodexHeadlessBackend(),
        }
        self.telemetry = telemetry
        self.operations_metrics = operations_metrics
        self.runtime_logs_enabled = runtime_logs_enabled
        self.timeout_s = timeout_s or float(os.environ.get(
            "ZF_KANBAN_AGENT_HEADLESS_TIMEOUT_S",
            str(DEFAULT_KANBAN_AGENT_HEADLESS_IDLE_TIMEOUT_S),
        ))

    def run_turn(
        self,
        *,
        backend: str,
        message: str,
        scope: str = "project",
        task_id: str = "",
        thread_key: str = "",
        context: dict[str, Any] | None = None,
        on_message: MessageCallback | None = None,
        thinking_level: str = "",
        permission_profile: str = "read_only",
    ) -> HeadlessTurnResult:
        backend = canonical_headless_backend(backend)
        permission_profile = normalize_permission_profile(permission_profile)
        if not backend or backend not in self.backends:
            return _backend_failure(backend or "unknown", "", "unsupported headless backend")
        adapter = self.backends[backend]
        if not adapter.available():
            return _backend_failure(backend, "", f"{backend} command is unavailable")
        if (
            permission_profile == "isolated_writer"
            and not (self.project_root / ".git").is_file()
        ):
            return _backend_failure(
                backend,
                "",
                "isolated_writer requires the selected project root to be a Git linked worktree",
            )

        thread = self.store.load(scope=scope, task_id=task_id, thread_key=thread_key)
        thread_id = str(thread["thread_id"])
        with self.store.locked(thread_id):
            thread = self.store.load(scope=scope, task_id=task_id, thread_key=thread_key)
            provider_session_id = self.store.provider_session_id(thread, backend=backend)
            context = context or {}
            base_snapshot = build_provider_permission_snapshot(
                backend=backend,
                permission_profile=permission_profile,
                cwd=self.project_root,
                project_id=str(context.get("project_id") or ""),
                conversation_id=str(context.get("conversation_id") or ""),
                thread_id=thread_id,
                run_id=str(context.get("turn_id") or ""),
                provider_session_id=provider_session_id,
                runtime_snapshot_ref=str(
                    context.get("runtime_snapshot_ref")
                    or context.get("snapshot_ref")
                    or ""
                ),
                role="kanban-agent",
                member_id="kanban-agent",
                source="kanban-agent.headless",
            )
            drift = provider_permission_drift(
                self.store.provider_permission_snapshot(thread, backend=backend),
                base_snapshot,
            )
            if provider_session_id and drift.get("status") == "blocking":
                result = HeadlessTurnResult(
                    ok=False,
                    status="permission_drift_blocked",
                    backend=backend,
                    thread_id=thread_id,
                    provider_session_id=provider_session_id,
                    reply="",
                    messages=[],
                    usage={},
                    resumed=True,
                    error=_permission_drift_block_reason(drift),
                    permission_snapshot=base_snapshot,
                    permission_drift=drift,
                )
                self.store.record_turn(thread, result=result, workdir=str(self.project_root))
                return result

            def pin(session_id: str) -> None:
                self.store.pin_provider_session(
                    thread,
                    backend=backend,
                    provider_session_id=session_id,
                    workdir=str(self.project_root),
                    status="running",
                    permission_snapshot=snapshot_with_provider_session(
                        base_snapshot,
                        session_id,
                    ),
                    permission_drift=drift,
                )

            prompt = self._build_prompt(message=message, task_id=task_id, context=context)
            telemetry_launch = self._prepare_telemetry(
                adapter=adapter,
                backend=backend,
                task_id=task_id,
                thread_id=thread_id,
                provider_session_id=provider_session_id,
                context=context,
            )
            started_at = time.monotonic()
            result = adapter.run_turn(
                prompt=prompt,
                cwd=self.project_root,
                system_prompt=self._system_prompt(permission_profile),
                thread_id=thread_id,
                provider_session_id=provider_session_id,
                on_session_id=pin,
                on_message=on_message,
                timeout_s=self.timeout_s,
                thinking_level=thinking_level,
                run_id=str(context.get("turn_id") or ""),
                run_thread_id=str(context.get("run_thread_id") or thread_key),
                project_id=str(context.get("project_id") or ""),
                conversation_id=str(context.get("conversation_id") or ""),
                permission_profile=permission_profile,
            )
            if result.status != "cancelled" and not result.ok and provider_session_id and not result.provider_session_id:
                fallback_reason = result.fallback_reason or "resume failed; retried fresh"
                retry = adapter.run_turn(
                    prompt=prompt,
                    cwd=self.project_root,
                    system_prompt=self._system_prompt(permission_profile),
                    thread_id=thread_id,
                    provider_session_id="",
                    on_session_id=pin,
                    on_message=on_message,
                    timeout_s=self.timeout_s,
                    thinking_level=thinking_level,
                    run_id=str(context.get("turn_id") or ""),
                    run_thread_id=str(context.get("run_thread_id") or thread_key),
                    project_id=str(context.get("project_id") or ""),
                    conversation_id=str(context.get("conversation_id") or ""),
                    permission_profile=permission_profile,
                )
                if retry.fallback_reason:
                    fallback_reason = retry.fallback_reason
                result = replace(retry, fallback_reason=fallback_reason)
            final_snapshot = snapshot_with_provider_session(
                base_snapshot,
                result.provider_session_id,
            )
            result = replace(
                result,
                permission_snapshot=final_snapshot,
                permission_drift=drift,
                telemetry=(
                    self._telemetry_result_payload(telemetry_launch)
                    if telemetry_launch is not None else {}
                ),
            )
            self.store.record_turn(thread, result=result, workdir=str(self.project_root))
            self._record_telemetry_result(
                telemetry_launch,
                result=result,
                duration_s=time.monotonic() - started_at,
            )
            return result

    def _prepare_telemetry(
        self,
        *,
        adapter: HeadlessBackend,
        backend: str,
        task_id: str,
        thread_id: str,
        provider_session_id: str,
        context: dict[str, Any],
    ) -> Any | None:
        if self.telemetry is None:
            return None
        operation_kind = str(context.get("operation_kind") or "kanban_turn")
        if operation_kind not in {"kanban_turn", "channel_turn", "sidecar_operation"}:
            operation_kind = "kanban_turn"
        operation = TelemetryOperationContextV1.interaction(
            operation_kind=operation_kind,
            correlation_id=str(context.get("trace_id") or ""),
            project_id=str(context.get("project_id") or ""),
            workflow_run_id=str(context.get("workflow_run_id") or context.get("turn_id") or ""),
            task_id=task_id,
            dispatch_id=str(context.get("turn_id") or ""),
            attempt_id=str(context.get("attempt_id") or ""),
            role_instance_id="kanban-agent",
            provider=backend,
            conversation_id=str(context.get("conversation_id") or ""),
            thread_id=thread_id,
            provider_session_id=provider_session_id,
        )
        launch = self.telemetry.launch(operation, route="headless")
        configure = getattr(adapter, "set_telemetry_env", None)
        if callable(configure):
            configure(launch.env)
        return launch

    @staticmethod
    def _telemetry_result_payload(launch: Any) -> dict[str, Any]:
        capability = launch.capability
        return {
            "capability": capability.to_dict(),
            "context": launch.context.public_identity(),
        }

    def _record_telemetry_result(
        self,
        launch: Any | None,
        *,
        result: HeadlessTurnResult,
        duration_s: float,
    ) -> None:
        capability = launch.capability if launch is not None else None
        context = launch.context.operation if launch is not None else None
        failure_class = "" if result.ok else _headless_failure_class(result.status)
        write_runtime_log(
            self.state_dir,
            level="INFO" if result.ok else "ERROR",
            component="headless-agent",
            message="provider turn completed" if result.ok else "provider turn failed",
            failure_class=failure_class,
            fields={
                "zaofu_correlation_id": context.correlation_id if context else "",
                "task_id": context.task_id if context else "",
                "workflow_run_id": context.workflow_run_id if context else "",
                "dispatch_id": context.dispatch_id if context else "",
                "attempt_id": context.attempt_id if context else "",
                "role_instance_id": "kanban-agent",
                "provider": result.backend,
                "provider_session_id": result.provider_session_id,
                "route": "headless",
                "operation_kind": context.operation_kind if context else "kanban_turn",
                "status": result.status,
            },
            enabled=self.runtime_logs_enabled,
        )
        if self.operations_metrics is None:
            return
        labels = {
            "provider": result.backend,
            "operation": context.operation_kind if context else "kanban_turn",
            "result": "completed" if result.ok else "failed",
        }
        if failure_class:
            labels["failure_class"] = failure_class
        self.operations_metrics.increment("zf_provider_operations_total", labels=labels)
        self.operations_metrics.observe(
            "zf_provider_operation_duration_seconds",
            duration_s,
            labels=labels,
        )

    def _system_prompt(self, permission_profile: str = "read_only") -> str:
        profile = normalize_permission_profile(permission_profile)
        cli_command = _headless_zf_cli_cmd()
        execution_contract = {
            "read_only": (
                "This turn is read-only. Inspect and explain, but do not edit files "
                "or run commands that mutate the workspace."
            ),
            "operator": (
                "This turn is an operator turn. You may inspect the project and "
                "propose controlled collaboration/runtime actions, but do not edit "
                "project source files."
            ),
            "artifact_writer": (
                "You may write only the artifact paths permitted by the supplied "
                "write policy."
            ),
            "project_writer": (
                "You may write only the project documentation, skill, task, and "
                "backlog paths permitted by the supplied write policy."
            ),
            "workspace_writer": (
                "You are authorized to implement code and run verification in the "
                "current project workspace. Do not commit, push, merge, or rewrite "
                "Git history unless the user explicitly asks through an authorized "
                "higher-permission turn."
            ),
            "isolated_writer": (
                "You are authorized to implement code and run verification only in "
                "the current isolated worktree. Do not modify another checkout, "
                "push, merge, or rewrite Git history."
            ),
            "dangerous_full": (
                "The operator explicitly authorized full project shell and Git "
                "access for this turn. Follow the request directly while preserving "
                "ZaoFu state ownership and repository safety rules."
            ),
        }[profile]
        return (
            "You are the ZaoFu Kanban Agent. You explain runtime projections and "
            "can perform normal project development when the active permission "
            f"profile allows it. Active permission profile: {profile}. "
            f"{execution_contract} "
            "Never mutate .zf truth, events.jsonl, kanban.json, feature_list.json, "
            "session state, or other Kernel-owned canonical state directly; request "
            "a sanctioned CLI or controlled action for those effects. "
            "For every ZaoFu CLI invocation, use the exact active-runtime command "
            f"`{cli_command}` followed by its arguments. Do not use a bare `zf` "
            "unless that exact command is literally `zf`; examples in skills that "
            "show bare `zf` are abbreviations, not executable authority. "
            "Read-only requests such as introduce yourself, explain, analyze, debug, "
            "diagnose, inspect, review a task, or ask why something happened must be "
            "answered without action_proposal or plan_request JSON. Do not include example "
            "action_proposal JSON in ordinary explanations; plain text is safer. "
            "When one to three unresolved owner choices materially change the route "
            "or parameters, end the turn with exactly one compact fenced JSON object "
            "containing plan_request. A single question may use {header, id, question, "
            "options, allow_other, reason}; multiple pure clarification questions use "
            "questions with one to three entries in that same shape. Set "
            "subject_type to exactly one of channel_setup, clarification, "
            "task_create, or task_workflow. Pure business/research/issue/refactor "
            "questions always use clarification; never invent a family-specific "
            "subject_type. Each question has two or three mutually exclusive "
            "options, exactly one recommended "
            "option first, and an optional free-form answer. Multi-question Plans "
            "cannot bind an action. Do not request secrets. A plan_request ends the turn and "
            "must never appear with action_proposal. Do not use a synchronous "
            "request_user_input call in this headless bridge: the durable answer "
            "will arrive as the next turn in the same provider session. Plan is "
            "clarification, not permission or approval, except for the bounded "
            "Channel setup contract below. "
            "When turn context contains plan_discussion, answer the user's visible "
            "message naturally against that exact pending Plan. Keep the Plan pending "
            "and do not repeat its JSON unless the discussion requires a revised "
            "question or options. "
            "If a state change is needed, describe the exact controlled action for "
            "the Web action gate. Only when the operator explicitly asks to create, "
            "track, or schedule work should you propose create-task. Decide that "
            "intent semantically in the operator's language; do not rely on English "
            "or Chinese keyword spelling. Negated requests, explanations, examples, "
            "and questions about how creation works are not creation requests. Ask a "
            "Plan question when the intent is ambiguous. The turn "
            "context contains workflow_route_catalog from the active expanded "
            "zf.yaml. Use the zf-workflow-start-planner method when available for "
            "semantic classification, clarification, and route recommendation, and "
            "use only route_id values present in that catalog. Never inspect, "
            "request, or fabricate a workflow action token, and never call "
            "`zf workflow start --apply`; provider turns may only preview/propose. "
            "Add "
            "payload.workflow_plan with a task-"
            "specific question and two or three options. Executable options contain "
            "route_id, label, description, recommended, and optional parameters; "
            "a no-run choice uses mode=defer. Do not put Channel creation in "
            "workflow_plan. The runtime creates the Task first, injects its real "
            "task_id, and publishes the Workflow Plan; Task approval does not start "
            "a Workflow. Every create-task proposal must set "
            "payload.execution_mode=workflow by default. Use execution_mode=direct "
            "only when the operator explicitly requests immediate legacy/direct "
            "dispatch. When the operator asks for tracking without execution, keep "
            "execution_mode=workflow and omit workflow_plan. When proposing "
            "an action, end the reply with exactly one compact fenced JSON object "
            "with action_proposal: {action, intent, payload, reason}; do not place "
            "action proposal JSON earlier in prose. For create-task and "
            'idea-to-product, intent must be {"decision": "propose_action", '
            '"source_quote": "<exact verbatim substring from the user semantic '
            'context that supports this proposal>"}. For product '
            "ideas, prefer action=idea-to-product with payload.objective. For "
            "workflow yaml changes or runtime restart/stop, propose "
            "workflow-config-* or runtime-* only as owner-approved/proposal-only "
            "actions. For creating work, prefer "
            "action=create-task with payload.title and payload.contract; the operator "
            "must confirm before the action runs. For a Task created from a Channel "
            "or Research artifact, copy the supplied lineage exactly into "
            "contract.spec_ref, contract.source_ref, contract.handoff_artifacts, and "
            "contract.evidence_contract.channel_prd_digest. These values must be "
            "non-empty, must not be fabricated, and must preserve the artifact ref "
            "and sha256 digest from turn context. The turn context contains a bounded "
            "canonical_channel_prds projection. Use it only after an explicit owner "
            "request to create a Task from that artifact. Set spec_ref and "
            "handoff_artifacts from item.artifact_ref, source_ref from item.source_ref, "
            "and channel_prd_digest from item.artifact_digest. When no item or "
            "multiple plausible items match, ask one Plan question instead of "
            "guessing. When exact Channel authority context asks to create a "
            "Task from its confirmed PRD, return subject_type=task_create with "
            "two or three options. The recommended option uses mode=propose, "
            "action=create-task, and a flat payload limited to title, objective, "
            "acceptance, acceptance_criteria, scope, explicit_non_goals, "
            "skills_required, priority, optional task_id, and optional "
            "workflow_plan; priority must be an integer from 1 through 5. When "
            "the owner asks to take this PRD into delivery, include workflow_plan "
            "with two or three choices from the active workflow_route_catalog. "
            "Each executable workflow_plan choice has id, label, description, "
            "recommended, route_id, objective, and optional parameters; its only "
            "no-run choice uses mode=defer. Do not include task_id, config_digest, "
            "or task_contract_digest in workflow_plan: the Kernel creates the "
            "Task first and rebinds those facts. Task confirmation only creates the "
            "Task and publishes a second Workflow Plan; that Workflow still needs "
            "a separate owner choice and confirmation to start. Put mode, action, "
            "and payload inside the option's effect object. Copy the selected canonical item's "
            "artifact_ref and artifact_digest into top-level plan_request fields "
            "channel_prd_ref and channel_prd_digest. Also set top-level "
            'channel_prd_intent={"decision":"bind_channel_prd",'
            '"source_quote":"<exact verbatim substring from the current user '
            'requirement chain explicitly requesting this PRD handoff>"}. '
            "Selectors and channel_prd_intent must appear together. Never add "
            "them for an unrelated Issue, Refactor, General, or Research Task "
            "merely because canonical_channel_prds is available. Do not nest a "
            "contract or channel_authority in that payload; the runtime binds "
            "the exact authority. Include a second no-action alternative with "
            "effect.mode=continue, not defer, and do not emit action_proposal "
            "in the same turn. scope may contain only raw repo-relative paths "
            "or globs; put all scope prose in objective. The scope must cover "
            "every mandatory acceptance criterion's implementation plus its "
            "test, runner/config, and evidence-producer paths, including planned "
            "paths that do not exist yet. Do not bind browser acceptance only to "
            "unit/build files or omit Playwright/evidence paths. task_create Plan is "
            "reserved for this authorized Channel PRD handoff. For an unrelated "
            "direct Task without an artifact, never use subject_type=task_create; "
            "emit a create-task action_proposal whose contract still needs "
            "behavior, verification, verification_tiers, and acceptance. "
            "verification must be an executable shell command from the repository "
            "root with no prose prefix. verification_tiers must contain one or "
            "more of static, runtime, e2e, or manual_evidence. When scope or "
            "affected_files changes product code, preserve an available product, "
            "spec, or source ref; if none exists, set a non-empty spec_skip_reason "
            "that explains why the bounded Task contract is the source authority. "
            "When a requirement would benefit from "
            "a new multi-role collaboration Channel, do not directly propose a "
            "low-level create action. Return one action-bound Channel setup Plan "
            "with submit_action=channel-create-and-start, submit_label='Create & "
            "start', allow_other=false, and two or three options. Every option must "
            "have label, description, and an exact option-level submit_payload "
            "containing template_id, an explicit mode, plus optional "
            "name/overrides. Include a "
            "non-empty top-level question. Do not wrap Channel options in effect; "
            "do not put submit_payload under effect. Every option must choose "
            "exactly one product mode: conversation for one default responder, "
            "clarification for facilitated relay, or multi_lens for bounded blind "
            "fanout and synthesis. Use mode=multi_lens "
            "inside submit_payload when the operator "
            "explicitly requests independent multi-role input or parallel review, "
            "never as effect.mode or submit_mode, and otherwise preserve the "
            "template mode; use clarification for an owner-question-led pass. The "
            "Channel default uses synthesis-adaptive passes: omit "
            "overrides.budget.max_rounds unless the operator explicitly requests a "
            "hard cap. Turn context contains channel_profile_catalog. You may select "
            "only a listed Profile with the same template role through "
            "overrides.role_overrides.<template_role>.profile_id; never invent a "
            "Profile, role, skill, provider, prompt, permission, or capability. "
            "To disable an optional template role, use only "
            "overrides.role_overrides.<template_role>.enabled=false; never emit "
            "disabled_roles. "
            "Add top-level discussion_seed containing only the "
            "business requirement the Channel should discuss; exclude setup "
            "instructions, option labels, and Plan JSON. Use each option "
            "description for its tradeoff; the "
            "runtime materializes and displays exact mode, initial routing/fanout, "
            "member roles, selected Profiles, member count, and adaptive/capped pass "
            "policy from submit_payload. The "
            "selected option is the "
            "exact confirmation: the runtime creates the Channel and members, posts "
            "the original user requirement, and starts discussion without another "
            "proposal or a manual first message. Built-in template ids are "
            "prd-clarification, research-review, architecture-review, quick-change, "
            "and incident-triage. For a direct non-chat API request with an already "
            "fixed configuration, use channel-create-and-start. For a discussion "
            "in an existing Channel, propose channel-discussion-start with "
            "payload.channel_id and payload.objective. Channel is an independent "
            "collaboration surface, not a Workflow preparation stage. For an "
            "existing Task, use subject_type=task_workflow and option effects with "
            "mode=propose, action=workflow-start, and exact payload "
            "{task_id,route_id,objective,config_digest,parameters}; the runtime "
            "binds the current task_contract_digest before display. The runtime turns "
            "the selected route into a separate Approve proposal. Put the question "
            "and options directly in top-level plan_request; do not add "
            "payload.workflow_plan or plan_request.payload. A no-action alternative "
            "uses effect.mode=continue with no action or payload, never defer. "
            "parameters may contain only executable adapter keys from this list: "
            f"{TASK_WORKFLOW_PARAMETER_KEYS_TEXT}. Keep business requirements, "
            "non-goals, verification prose, and output descriptions in the Task "
            "contract or objective instead of inventing parameter keys. If source/input "
            "refs, expected output, or another required route parameter is unresolved, "
            "ask one bounded clarification Plan with up to three questions before "
            "emitting the task_workflow choice. A new topology must use the "
            "workflow-config proposal path. Do not emit workflow-invoke(pattern_id) "
            "as the normal Kanban product action; it is a compatibility adapter "
            "behind workflow-start. Preparatory Research is Request-first. For a "
            "Channel-bound Research request, first propose workflow-request with "
            "the exact channel_id/thread_id and conversation context. Only after "
            "that controlled action returns a request_id and request_revision may "
            "you propose create-task with execution_mode=workflow, those exact "
            "Request fields, and a Research workflow_plan. The later research-start "
            "or workflow-start must carry the same Request revision; never invent "
            "or replace its origin binding. Standalone knowledge Research may omit "
            "a Request, but its result cannot be adopted into requirements. "
            "Never report these effects as completed before "
            "the controlled action is approved. Contract discipline "
            "(ZF-E2E-RACING-P2 2026-07-11: a structured verification of bare "
            "'npm test' failed from the repo root and burned four rework "
            "rounds): contract.verification is machine-executed from the "
            "repository root — it must run as-is from there; if the command "
            "depends on a subdirectory, embed the directory in the command "
            "itself (cd <subdir> && ... or an equivalent flag). The structured "
            "verification must state the same command as the acceptance text; "
            "re-check both against each other before proposing. "
            "Keep answers concise and evidence-oriented."
        )

    def _build_prompt(
        self,
        *,
        message: str,
        task_id: str,
        context: dict[str, Any],
    ) -> str:
        return "\n".join([
            "ZaoFu Kanban Agent turn",
            f"project_root: {self.project_root}",
            f"state_dir: {self.state_dir}",
            f"task_id: {task_id or ''}",
            f"thread_key: {str(context.get('thread_key') or '')}",
            f"zf_cli_cmd: {_headless_zf_cli_cmd()}",
            f"context: {json.dumps(context, ensure_ascii=False, sort_keys=True)}",
            "",
            "User message:",
            message,
        ])


def _permission_drift_block_reason(drift: dict[str, Any]) -> str:
    fields = [
        str(item.get("field") or "")
        for item in drift.get("items", []) or []
        if isinstance(item, dict) and item.get("severity") == "blocking"
    ]
    suffix = f": {', '.join(field for field in fields if field)}" if fields else ""
    return f"provider permission snapshot drift blocked resume{suffix}"


def canonical_headless_backend(value: str) -> str:
    backend = str(value or "").strip()
    aliases = {
        "claude": "claude-headless",
        "claude-code": "claude-headless",
        "claude-code-headless": "claude-headless",
        "claude_headless": "claude-headless",
        "codex": "codex-headless",
        "codex-app-server": "codex-headless",
        "codex_headless": "codex-headless",
    }
    if backend in aliases:
        return aliases[backend]
    if backend in {"claude-headless", "codex-headless"}:
        return backend
    return ""


def _backend_failure(backend: str, thread_id: str, reason: str) -> HeadlessTurnResult:
    return HeadlessTurnResult(
        ok=False,
        status="failed",
        backend=backend,
        thread_id=thread_id,
        provider_session_id="",
        reply="",
        messages=[],
        usage={},
        error=reason,
    )


def _cancelled_result(
    backend: str,
    thread_id: str,
    *,
    provider_session_id: str = "",
    messages: list[HeadlessMessage] | None = None,
    usage: dict[str, Any] | None = None,
    resumed: bool = False,
) -> HeadlessTurnResult:
    return HeadlessTurnResult(
        ok=False,
        status="cancelled",
        backend=backend,
        thread_id=thread_id,
        provider_session_id=provider_session_id,
        reply="",
        messages=messages or [],
        usage=usage or {},
        resumed=resumed,
        error="cancelled by operator",
    )


def _build_claude_input(prompt: str) -> str:
    return json.dumps({
        "type": "user",
        "message": {
            "role": "user",
            "content": [{"type": "text", "text": prompt}],
        },
    }, ensure_ascii=False) + "\n"


def _provider_completion_incomplete(status: str, reason: str) -> bool:
    normalized_status = (
        str(status or "").strip().lower().replace("-", "_").replace(" ", "_")
    )
    normalized_reason = (
        str(reason or "").strip().lower().replace("-", "_").replace(" ", "_")
    )
    if normalized_status in {"incomplete", "truncated", "length"}:
        return True
    markers = (
        "max_tokens",
        "max_output_tokens",
        "max_turns",
        "context_length",
        "context_window",
        "context_limit",
        "token_limit",
        "output_length",
        "response_incomplete",
    )
    return any(marker in normalized_reason for marker in markers)


@dataclass(frozen=True)
class _ParsedClaudeStream:
    provider_session_id: str
    reply: str
    messages: list[HeadlessMessage]
    usage: dict[str, Any]
    is_error: bool = False
    error: str = ""
    incomplete: bool = False
    completion_reason: str = ""


def _parse_claude_stream(
    stdout: str,
    *,
    on_session_id: SessionCallback,
    on_message: MessageCallback | None = None,
) -> _ParsedClaudeStream:
    accumulator = _ClaudeStreamAccumulator(
        on_session_id=on_session_id,
        on_message=on_message,
    )
    for raw_line in stdout.splitlines():
        accumulator.observe_line(raw_line)
    return accumulator.to_result()


class _ClaudeStreamAccumulator:
    def __init__(
        self,
        *,
        on_session_id: SessionCallback,
        on_message: MessageCallback | None,
    ) -> None:
        self.on_session_id = on_session_id
        self.on_message = on_message
        self.messages: list[HeadlessMessage] = []
        self.text_parts: list[str] = []
        self.usage: dict[str, Any] = {}
        self.provider_session_id = ""
        self.result_text = ""
        self.is_error = False
        self.error = ""
        self.incomplete = False
        self.completion_reason = ""
        # B-STREAM-01 partial-message streaming: text streamed so far per
        # content-block index, and indices that already emitted a redacted
        # thinking signal. Both dedupe against the final assistant block, which
        # still arrives in full even when partials are enabled.
        self._streamed_by_index: dict[int, str] = {}
        self._thinking_signaled: set[int] = set()

    def observe_line(self, raw_line: str) -> None:
        line = raw_line.strip()
        if not line:
            return
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            return
        if not isinstance(msg, dict):
            return
        self.observe_message(msg)

    def observe_message(self, msg: dict[str, Any]) -> None:
        msg_type = str(msg.get("type") or "")
        session_id = str(msg.get("session_id") or msg.get("sessionId") or "")
        if session_id:
            self.provider_session_id = session_id
            self.on_session_id(session_id)
        if msg_type == "system":
            self._emit(HeadlessMessage(type="status", session_id=session_id, raw=msg))
            return
        if msg_type == "stream_event":
            event = msg.get("event")
            if isinstance(event, dict):
                self._observe_stream_event(event)
            return
        if msg_type == "assistant":
            assistant_message = (
                msg.get("message")
                if isinstance(msg.get("message"), dict)
                else {}
            )
            assistant_stop_reason = str(
                assistant_message.get("stop_reason")
                or assistant_message.get("stopReason")
                or ""
            )
            if assistant_stop_reason:
                self.completion_reason = assistant_stop_reason
                self.incomplete = _provider_completion_incomplete(
                    "",
                    assistant_stop_reason,
                )
            content = assistant_message.get("content", [])
            for idx, block in enumerate(content if isinstance(content, list) else []):
                if not isinstance(block, dict):
                    continue
                block_type = str(block.get("type") or "")
                if block_type == "text":
                    text = str(block.get("text") or "")
                    if text:
                        self.text_parts.append(text)
                        self._emit_final_text(idx, text, raw=block)
                elif block_type == "thinking":
                    # Do not persist or render raw chain-of-thought. The event
                    # tells the UI the provider is making progress without
                    # turning hidden reasoning into user-facing transcript. With
                    # partials a thinking_delta may already have signalled this
                    # block — don't double-signal.
                    if idx not in self._thinking_signaled:
                        self._thinking_signaled.add(idx)
                        self._emit(
                            HeadlessMessage(
                                type="thinking",
                                content="thinking",
                                raw={"type": "thinking", "redacted": True},
                            )
                        )
                elif block_type == "tool_use":
                    self._emit(
                        HeadlessMessage(
                            type="tool_use",
                            tool=str(block.get("name") or ""),
                            input=block.get("input"),
                            raw=block,
                        )
                    )
            return
        if msg_type == "user":
            content = msg.get("message", {}).get("content", [])
            for block in content if isinstance(content, list) else []:
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    self._emit(
                        HeadlessMessage(
                            type="tool_result",
                            output=str(block.get("content") or ""),
                            raw=block,
                        )
                    )
            return
        if msg_type == "result":
            self.result_text = str(msg.get("result") or msg.get("result_text") or "")
            self.usage = dict(msg.get("usage") or {})
            self.is_error = bool(msg.get("is_error") or msg.get("isError"))
            self.completion_reason = str(
                msg.get("stop_reason")
                or msg.get("stopReason")
                or self.completion_reason
                or msg.get("subtype")
                or ""
            )
            self.incomplete = self.incomplete or _provider_completion_incomplete(
                "", self.completion_reason
            )
            if self.is_error:
                self.error = self.result_text
            return

    def _observe_stream_event(self, event: dict[str, Any]) -> None:
        # B-STREAM-01: token-level deltas from --include-partial-messages.
        if str(event.get("type") or "") != "content_block_delta":
            return
        raw_index = event.get("index")
        idx = raw_index if isinstance(raw_index, int) else 0
        delta = event.get("delta")
        if not isinstance(delta, dict):
            return
        delta_type = str(delta.get("type") or "")
        if delta_type == "text_delta":
            text = str(delta.get("text") or "")
            if text:
                self._streamed_by_index[idx] = (
                    self._streamed_by_index.get(idx, "") + text
                )
                self._emit(HeadlessMessage(type="text", content=text, raw=event))
        elif delta_type == "thinking_delta":
            # One redacted progress signal per thinking block; never surface raw
            # chain-of-thought, even token-by-token.
            if idx not in self._thinking_signaled:
                self._thinking_signaled.add(idx)
                self._emit(
                    HeadlessMessage(
                        type="thinking",
                        content="thinking",
                        raw={"type": "thinking", "redacted": True},
                    )
                )

    def _emit_final_text(self, idx: int, full: str, *, raw: dict[str, Any]) -> None:
        # The full assistant text block still arrives even with partials on.
        # Emit only the suffix not already streamed token-by-token (mirrors the
        # Codex item/completed dedup) so the bubble isn't rendered twice.
        streamed = self._streamed_by_index.get(idx, "")
        if streamed and full.startswith(streamed):
            suffix = full[len(streamed):]
            if suffix:
                self._emit(HeadlessMessage(type="text", content=suffix, raw=raw))
            return
        if streamed:
            # Provider drift: partials didn't prefix the final block. Don't drop
            # content — emit the full block and log so the mismatch is visible.
            logger.warning(
                "claude partial-stream prefix mismatch at index %s; emitting "
                "full block (streamed=%d chars, full=%d chars)",
                idx,
                len(streamed),
                len(full),
            )
        self._emit(HeadlessMessage(type="text", content=full, raw=raw))

    def _emit(self, message: HeadlessMessage) -> None:
        self.messages.append(message)
        if self.on_message:
            self.on_message(message)

    def to_result(self) -> _ParsedClaudeStream:
        reply = self.result_text or "\n".join(self.text_parts).strip()
        return _ParsedClaudeStream(
            provider_session_id=self.provider_session_id,
            reply=reply,
            messages=self.messages,
            usage=self.usage,
            is_error=self.is_error,
            error=self.error,
            incomplete=self.incomplete,
            completion_reason=self.completion_reason,
        )


def _stream_claude_process(
    process: subprocess.Popen[str],
    *,
    prompt: str,
    timeout_s: float,
    on_session_id: SessionCallback,
    on_message: MessageCallback | None,
) -> tuple[_ParsedClaudeStream, str, bool]:
    accumulator = _ClaudeStreamAccumulator(
        on_session_id=on_session_id,
        on_message=on_message,
    )
    stdout_queue: queue.Queue[str | None] = queue.Queue()
    stderr_parts: list[str] = []

    def read_stdout() -> None:
        try:
            if process.stdout is not None:
                for line in process.stdout:
                    stdout_queue.put(line)
        finally:
            stdout_queue.put(None)

    def read_stderr() -> None:
        if process.stderr is None:
            return
        for line in process.stderr:
            stderr_parts.append(line.rstrip())
            del stderr_parts[:-80]

    threading.Thread(target=read_stdout, daemon=True).start()
    threading.Thread(target=read_stderr, daemon=True).start()

    if process.stdin is None:
        raise OSError("claude stdin pipe is unavailable")
    try:
        process.stdin.write(_build_claude_input(prompt))
        process.stdin.close()
    except BrokenPipeError:
        # The backend closed stdin before we finished writing the prompt
        # (e.g. it produced its full result and exited first). The stdout
        # reader thread still captures the result, so a broken *input* pipe
        # is not a turn failure. Surfaced under full-suite load where a
        # print-and-exit backend races the prompt write.
        pass

    deadline = time.monotonic() + timeout_s
    stdout_done = False
    timed_out = False
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            timed_out = True
            break
        try:
            line = stdout_queue.get(timeout=min(remaining, 0.1))
        except queue.Empty:
            if stdout_done and process.poll() is not None:
                break
            continue
        if line is None:
            stdout_done = True
            if process.poll() is not None:
                break
            continue
        accumulator.observe_line(line)
        # A2-1: idle-timeout semantics (parity with the codex app-server path,
        # _await_turn). A create-task turn spends its first minutes grounding a
        # contract via many tool-call/stream frames before the first proposal;
        # a wall-clock cap killed that actively-streaming turn at timeout_s and
        # returned status:failed with zero proposal. Reset the deadline on each
        # observed frame so only true silence (no output for timeout_s) trips
        # the timeout. Runaway protection stays with --max-turns, not here.
        deadline = time.monotonic() + timeout_s

    if timed_out and process.poll() is None:
        process.kill()
    try:
        process.wait(timeout=1)
    except subprocess.TimeoutExpired:
        timed_out = True
        process.kill()
        try:
            process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            pass

    while True:
        try:
            line = stdout_queue.get_nowait()
        except queue.Empty:
            break
        if line:
            accumulator.observe_line(line)

    return accumulator.to_result(), "\n".join(stderr_parts), timed_out


def _codex_turn_completion(item: dict[str, Any]) -> tuple[bool, str]:
    params = item.get("params") if isinstance(item.get("params"), dict) else {}
    turn = params.get("turn") if isinstance(params.get("turn"), dict) else {}
    status = str(turn.get("status") or "").strip().lower()
    details = (
        turn.get("incompleteDetails")
        if isinstance(turn.get("incompleteDetails"), dict)
        else turn.get("incomplete_details")
        if isinstance(turn.get("incomplete_details"), dict)
        else {}
    )
    reason = str(
        turn.get("stopReason")
        or turn.get("stop_reason")
        or turn.get("completionReason")
        or turn.get("completion_reason")
        or details.get("reason")
        or _extract_codex_turn_error(turn)
        or status
    )
    return _provider_completion_incomplete(status, reason), reason


class _CodexRpcClient:
    def __init__(
        self,
        args: list[str],
        *,
        cwd: Path,
        timeout_s: float,
        tool_timeout_s: float,
        permission_profile: str = "read_only",
        env: dict[str, str] | None = None,
    ) -> None:
        self.args = args
        self.cwd = cwd
        self.timeout_s = timeout_s
        self.tool_timeout_s = tool_timeout_s
        self.permission_profile = normalize_permission_profile(permission_profile)
        self.env = dict(env or _headless_subprocess_env())
        self.write_policy = permission_profile_write_policy(self.permission_profile)
        self.process: subprocess.Popen[str] | None = None
        self._queue: queue.Queue[dict[str, Any]] = queue.Queue()
        self._next_id = 0
        self._reply_parts: list[str] = []
        self._completed_reply = ""
        self._usage: dict[str, Any] = {}
        self._stderr_parts: list[str] = []
        self.thread_id = ""
        self._in_flight_tools = 0

    def start(self) -> None:
        self.process = subprocess.Popen(
            self.args,
            cwd=str(self.cwd),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=self.env,
        )
        thread = threading.Thread(target=self._reader, daemon=True)
        thread.start()
        stderr_thread = threading.Thread(target=self._drain_stderr, daemon=True)
        stderr_thread.start()

    def close(self) -> None:
        process = self.process
        if process is None:
            return
        if process.poll() is None:
            try:
                process.terminate()
                process.wait(timeout=1)
            except Exception:
                process.kill()

    def request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        self._next_id += 1
        req_id = self._next_id
        self._write({"jsonrpc": "2.0", "id": req_id, "method": method, "params": params})
        deadline = time.monotonic() + self.timeout_s
        while time.monotonic() < deadline:
            item = self._get_until(deadline)
            if item is None:
                self._raise_if_process_exited()
                continue
            if item.get("id") == req_id:
                if "error" in item:
                    raise RuntimeError(str(item["error"]))
                result = item.get("result")
                return result if isinstance(result, dict) else {}
            if "id" in item and item.get("method"):
                self._handle_server_request(item)
                continue
            self._handle_notification(item)
        stderr = _codex_stderr_tail(self._stderr_parts)
        raise RuntimeError(_codex_timeout_error("request", timeout_s=self.timeout_s, method=method, stderr=stderr))

    def notify(self, method: str) -> None:
        self._write({"jsonrpc": "2.0", "method": method})

    def wait_turn(
        self,
        *,
        on_message: MessageCallback | None = None,
    ) -> tuple[str, dict[str, Any], bool, str]:
        timeout_budget_s = self._inactivity_timeout_s()
        deadline = time.monotonic() + timeout_budget_s
        while True:
            item = self._get_until(deadline)
            if item is None:
                self._raise_if_process_exited()
                if time.monotonic() >= deadline:
                    break
                continue
            method = str(item.get("method") or "")
            if "id" in item and method:
                self._handle_server_request(item)
                timeout_budget_s = self._inactivity_timeout_s()
                deadline = time.monotonic() + timeout_budget_s
                continue
            message = self._handle_notification(item)
            timeout_budget_s = self._inactivity_timeout_s()
            deadline = time.monotonic() + timeout_budget_s
            if message is not None and on_message is not None:
                on_message(message)
            if method.endswith("turn/completed") or method == "turn/completed":
                incomplete, completion_reason = _codex_turn_completion(item)
                if not incomplete:
                    self._raise_if_turn_failed(item)
                reply = self._completed_reply or "".join(part for part in self._reply_parts if part)
                return (
                    reply.strip(),
                    self._usage,
                    incomplete,
                    completion_reason,
                )
        stderr = _codex_stderr_tail(self._stderr_parts)
        raise RuntimeError(_codex_timeout_error(
            "turn",
            timeout_s=timeout_budget_s,
            stderr=stderr,
            idle=True,
        ))

    def _inactivity_timeout_s(self) -> float:
        if self._in_flight_tools > 0:
            return self.tool_timeout_s
        return self.timeout_s

    def _write(self, payload: dict[str, Any]) -> None:
        process = self.process
        if process is None or process.stdin is None:
            raise RuntimeError("codex app-server is not running")
        process.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
        process.stdin.flush()

    def _reader(self) -> None:
        process = self.process
        if process is None or process.stdout is None:
            return
        for line in process.stdout:
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                self._queue.put(payload)

    def _drain_stderr(self) -> None:
        process = self.process
        if process is None or process.stderr is None:
            return
        for line in process.stderr:
            self._stderr_parts.append(line.rstrip())
            self._stderr_parts = self._stderr_parts[-80:]

    def _get_until(self, deadline: float) -> dict[str, Any] | None:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return None
        try:
            return self._queue.get(timeout=min(remaining, 0.5))
        except queue.Empty:
            return None

    def _raise_if_process_exited(self) -> None:
        process = self.process
        if process is None:
            return
        returncode = process.poll()
        if returncode is None:
            return
        stderr = _codex_stderr_tail(self._stderr_parts)
        suffix = f": {stderr}" if stderr else ""
        raise RuntimeError(f"codex app-server exited with code {returncode}{suffix}")

    def _handle_notification(self, item: dict[str, Any]) -> HeadlessMessage | None:
        method = str(item.get("method") or "")
        params = item.get("params")
        if not isinstance(params, dict):
            return None
        if method.endswith("tokenUsage/updated"):
            usage = params.get("usage") or params
            if isinstance(usage, dict):
                self._usage.update(usage)
            return None
        if method.endswith("turn/started"):
            return HeadlessMessage(
                type="status",
                content="running",
                session_id=self.thread_id,
                raw=item,
            )
        if method.endswith("item/agentMessage/delta"):
            text = str(params.get("delta") or "")
            if text:
                self._reply_parts.append(text)
                return HeadlessMessage(type="text", content=text, raw=item)
            return None
        if method.endswith("item/started"):
            message = _codex_item_message(item, message_type="tool_use")
            if message is not None:
                self._in_flight_tools += 1
                return message
        if method.endswith("item/completed"):
            message = _codex_item_message(item, message_type="tool_result")
            if message is not None:
                if message.type == "tool_result":
                    self._in_flight_tools = max(0, self._in_flight_tools - 1)
                return message
        if method.endswith("item/completed"):
            item = params.get("item")
            if isinstance(item, dict) and item.get("type") == "agentMessage":
                text = _extract_text(item)
                if text:
                    self._completed_reply = text
                    streamed = "".join(part for part in self._reply_parts if part)
                    if not streamed:
                        self._reply_parts.append(text)
                        return HeadlessMessage(type="text", content=text, raw=item)
                    if text.startswith(streamed):
                        suffix = text[len(streamed):]
                        if suffix:
                            self._reply_parts.append(suffix)
                            return HeadlessMessage(type="text", content=suffix, raw=item)
            return None
        if method.endswith("item/toolCall/started") or method.endswith("item/toolCall/completed"):
            item_payload = params.get("item") if isinstance(params.get("item"), dict) else params
            if isinstance(item_payload, dict):
                if method.endswith("started"):
                    self._in_flight_tools += 1
                else:
                    self._in_flight_tools = max(0, self._in_flight_tools - 1)
                return HeadlessMessage(
                    type="tool_use" if method.endswith("started") else "tool_result",
                    tool=str(item_payload.get("name") or item_payload.get("tool") or ""),
                    input=item_payload.get("input"),
                    output=str(item_payload.get("output") or ""),
                    raw=item,
                )
        return None

    def _raise_if_turn_failed(self, item: dict[str, Any]) -> None:
        params = item.get("params")
        if not isinstance(params, dict):
            return
        turn = params.get("turn")
        if not isinstance(turn, dict):
            return
        status = str(turn.get("status") or "").strip().lower()
        if status in {"", "completed"}:
            return
        if status == "inprogress":
            return
        message = _extract_codex_turn_error(turn) or status
        raise RuntimeError(f"codex turn {status}: {message}")

    def _handle_server_request(self, item: dict[str, Any]) -> None:
        request_id = item.get("id")
        if request_id is None:
            return
        method = str(item.get("method") or "")
        if not _is_codex_approval_method(method):
            message = (
                "Codex request_user_input is unavailable on the durable "
                "headless bridge; emit plan_request and wait for the next "
                "provider turn."
                if method.endswith("item/tool/requestUserInput")
                else f"unsupported Codex server request: {method or '<empty>'}"
            )
            self._write({
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": -32601, "message": message},
            })
            return
        decision = self._approval_decision(method, item.get("params"))
        self._write({
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {"decision": decision},
        })

    def _approval_decision(self, method: str, params: object) -> str:
        override = os.environ.get("ZF_KANBAN_AGENT_CODEX_HEADLESS_APPROVAL_DECISION")
        if override is not None:
            return _codex_decision_for_method(method, allow=_truthy_value(override), raw=override)
        allow = self._approval_allowed(method, params)
        return _codex_decision_for_method(method, allow=allow)

    def _approval_allowed(self, method: str, params: object) -> bool:
        if self.permission_profile in {"read_only", "operator"}:
            return False
        if self.permission_profile == "dangerous_full":
            return True
        if method == "applyPatchApproval":
            return _codex_file_changes_allowed(
                params,
                cwd=self.cwd,
                write_policy=self.write_policy,
                allow_empty=False,
            )
        if method.endswith("item/fileChange/requestApproval"):
            return _codex_file_changes_allowed(
                params,
                cwd=self.cwd,
                write_policy=self.write_policy,
                allow_empty=False,
            )
        if self.permission_profile in {"workspace_writer", "isolated_writer"}:
            return method == "execCommandApproval" or method.endswith(
                "item/commandExecution/requestApproval"
            )
        return False


def _is_codex_approval_method(method: str) -> bool:
    return method in {"applyPatchApproval", "execCommandApproval"} or any(
        method.endswith(suffix)
        for suffix in (
            "item/fileChange/requestApproval",
            "item/commandExecution/requestApproval",
        )
    )


def _codex_decision_for_method(method: str, *, allow: bool, raw: str = "") -> str:
    token = raw.strip() if raw else ""
    if method in {"applyPatchApproval", "execCommandApproval"}:
        if token in {"approved", "denied", "timed_out", "abort", "approved_for_session"}:
            return token
        if token in {"accept", "acceptForSession"}:
            return "approved"
        if token in {"decline", "cancel"}:
            return "denied"
        return "approved" if allow else "denied"
    if token in {"accept", "acceptForSession", "decline", "cancel"}:
        return token
    if token in {"approved", "approved_for_session"}:
        return "accept"
    if token in {"denied", "timed_out", "abort"}:
        return "decline"
    return "accept" if allow else "decline"


def _truthy_value(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "y", "on", "allow", "accept", "approved", "approve"}


def _codex_file_changes_allowed(
    params: object,
    *,
    cwd: Path,
    write_policy: dict[str, Any],
    allow_empty: bool,
) -> bool:
    if not isinstance(params, dict):
        return False
    paths: list[str] = []
    file_changes = params.get("fileChanges")
    if isinstance(file_changes, dict):
        paths.extend(str(path) for path in file_changes.keys() if str(path).strip())
    grant_root = str(params.get("grantRoot") or "").strip()
    if grant_root:
        paths.append(grant_root)
    if not paths:
        return allow_empty
    return all(_path_allowed_by_write_policy(path, cwd=cwd, write_policy=write_policy) for path in paths)


def _path_allowed_by_write_policy(path: str, *, cwd: Path, write_policy: dict[str, Any]) -> bool:
    allowed_paths = write_policy.get("allowed_write_paths")
    if not isinstance(allowed_paths, list):
        return False
    if "*" in {str(item) for item in allowed_paths}:
        return True
    target = _resolve_policy_path(path, cwd=cwd)
    for raw_allowed in allowed_paths:
        allowed = str(raw_allowed or "").strip()
        if not allowed:
            continue
        allowed_root = _resolve_policy_path(allowed, cwd=cwd)
        if _is_relative_to(target, allowed_root):
            return True
    return False


def _resolve_policy_path(value: str, *, cwd: Path) -> Path:
    path = Path(value)
    if not path.is_absolute():
        path = cwd / path
    return path.resolve(strict=False)


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _extract_thread_id(result: dict[str, Any]) -> str:
    for key in ("threadId", "thread_id", "id"):
        value = result.get(key)
        if value:
            return str(value)
    thread = result.get("thread")
    if isinstance(thread, dict):
        return _extract_thread_id(thread)
    return ""


def _extract_codex_turn_error(turn: dict[str, Any]) -> str:
    error = turn.get("error")
    if not isinstance(error, dict):
        return ""
    message = str(error.get("message") or "").strip()
    details = str(error.get("additionalDetails") or error.get("additional_details") or "").strip()
    if message and details:
        return f"{message}: {details}"
    return message or details


def _codex_item_message(item: dict[str, Any], *, message_type: str) -> HeadlessMessage | None:
    params = item.get("params")
    if not isinstance(params, dict):
        return None
    payload = params.get("item")
    if not isinstance(payload, dict):
        payload = params
    item_type = str(payload.get("type") or "")
    # Non-tool items must not render as tool panels (rich-card noise, doc 98 §9):
    # codex streams reasoning + the user echo as items too. agentMessage/userMessage
    # carry no tool semantics; reasoning becomes a thinking chunk on completion (so
    # it shows in the 🧠 panel, not a fake "reasoning" tool call).
    if item_type in ("agentMessage", "userMessage", "agentMessageDelta"):
        return None
    if item_type == "reasoning":
        if message_type != "tool_result":  # emit once, on item/completed
            return None
        text = _extract_text(payload) or str(
            payload.get("text") or payload.get("content") or "")
        if not text.strip():
            return None
        return HeadlessMessage(type="thinking", content=text,
                               raw={"item_type": item_type})
    if item_type == "commandExecution":
        tool = "exec_command"
    elif item_type == "fileChange":
        tool = "patch_apply"
    elif item_type == "mcpToolCall":
        tool = str(payload.get("name") or "mcp_tool")
    else:
        tool = str(
            payload.get("name")
            or payload.get("tool")
            or item_type
            or "tool"
        )
    call_id = str(payload.get("id") or payload.get("callId") or payload.get("call_id") or "")
    input_payload = payload.get("input")
    if input_payload is None and payload.get("command"):
        input_payload = {"command": payload.get("command")}
    output = str(
        payload.get("output")
        or payload.get("aggregatedOutput")
        or payload.get("result")
        or ""
    )
    raw = {
        "method": item.get("method"),
        "item_type": item_type,
        "call_id": call_id,
    }
    return HeadlessMessage(
        type=message_type,
        tool=tool,
        input=input_payload,
        output=output,
        raw=raw,
    )


def _extract_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "\n".join(part for part in (_extract_text(item) for item in value) if part)
    if not isinstance(value, dict):
        return ""
    for key in ("text", "delta", "content", "message", "output"):
        found = _extract_text(value.get(key))
        if found:
            return found
    item = value.get("item")
    if item is not value:
        return _extract_text(item)
    return ""


def _tail(text: str, limit: int = 4000) -> str:
    if len(text) <= limit:
        return text.strip()
    return text[-limit:].strip()
