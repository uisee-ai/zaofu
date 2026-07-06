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

from zf.core.state.atomic_io import atomic_write_text
from zf.core.state.locks import FileLock
from zf.web.agent_session_runtime import (
    agent_session_process,
    agent_session_run_cancelled,
    run_key,
)

logger = logging.getLogger(__name__)
from zf.runtime.provider_permissions import (
    build_provider_permission_snapshot,
    claude_permission_mode_for_profile,
    codex_security_config_for_profile,
    provider_permission_drift,
    snapshot_with_provider_session,
)
from zf.runtime.channel_contracts import (
    normalize_permission_profile,
    permission_profile_write_policy,
)


SessionCallback = Callable[[str], None]


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

    def __init__(self, *, command: str | None = None, max_turns: int = 8) -> None:
        self.command = command or os.environ.get(
            "ZF_KANBAN_AGENT_CLAUDE_HEADLESS_CMD",
            os.environ.get("ZF_KANBAN_AGENT_CLAUDE_CMD", "claude"),
        )
        self.max_turns = max_turns

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
                env=dict(os.environ),
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
        ok = process.returncode == 0 and not parsed.is_error
        status = "completed" if ok else "failed"
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
            error="" if ok else (parsed.error or error or f"claude exited {process.returncode}"),
        )


def _claude_permission_mode_for_profile(permission_profile: str) -> str:
    return claude_permission_mode_for_profile(permission_profile)


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

    def __init__(self, *, command: str | None = None) -> None:
        self.command = command or os.environ.get(
            "ZF_KANBAN_AGENT_CODEX_HEADLESS_CMD",
            os.environ.get("ZF_KANBAN_AGENT_CODEX_CMD", "codex"),
        )

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
            permission_profile=permission_profile,
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
                        "version": "0.1.0",
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
                reply, usage = client.wait_turn(on_message=on_message)
                if agent_session_run_cancelled(session_key):
                    return _cancelled_result(
                        self.backend_id,
                        thread_id,
                        provider_session_id=codex_thread_id,
                        usage=usage,
                        resumed=resumed,
                    )
                return HeadlessTurnResult(
                    ok=True,
                    status="completed",
                    backend=self.backend_id,
                    thread_id=thread_id,
                    provider_session_id=codex_thread_id,
                    reply=reply,
                    messages=[HeadlessMessage(type="text", content=reply)] if reply else [],
                    usage=usage,
                    resumed=resumed,
                    fallback_reason=fallback_reason,
                )
        except Exception as exc:
            if agent_session_run_cancelled(session_key):
                return _cancelled_result(self.backend_id, thread_id, resumed=resumed)
            error = str(exc)
            status = _codex_failure_status(error)
            return HeadlessTurnResult(
                ok=False,
                status=status,
                backend=self.backend_id,
                thread_id=thread_id,
                provider_session_id="",
                reply="",
                messages=[],
                usage={},
                resumed=resumed,
                fallback_reason=fallback_reason,
                error=_codex_sandbox_error_with_hint(error) if status == "sandbox_unsupported" else error,
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
        self.timeout_s = timeout_s or float(os.environ.get("ZF_KANBAN_AGENT_HEADLESS_TIMEOUT_S", "120"))

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
        if not backend or backend not in self.backends:
            return _backend_failure(backend or "unknown", "", "unsupported headless backend")
        adapter = self.backends[backend]
        if not adapter.available():
            return _backend_failure(backend, "", f"{backend} command is unavailable")

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
            result = adapter.run_turn(
                prompt=prompt,
                cwd=self.project_root,
                system_prompt=self._system_prompt(),
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
                    system_prompt=self._system_prompt(),
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
            )
            self.store.record_turn(thread, result=result, workdir=str(self.project_root))
            return result

    def _system_prompt(self) -> str:
        return (
            "You are the ZaoFu Kanban Agent. You explain runtime projections and "
            "propose controlled actions, but you never mutate .zf truth directly. "
            "Read-only requests such as introduce yourself, explain, analyze, debug, "
            "diagnose, inspect, review a task, or ask why something happened must be "
            "answered without action_proposal JSON. Do not include example "
            "action_proposal JSON in ordinary explanations; plain text is safer. "
            "If a state change is needed, describe the exact controlled action for "
            "the Web action gate. Only when the operator explicitly asks to create, "
            "track, or schedule work should you propose create-task. When proposing "
            "an action, include a compact JSON "
            "object with action_proposal: {action, payload, reason}. For product "
            "ideas, prefer action=idea-to-product with payload.objective. For "
            "workflow yaml changes, provider dev chat, or runtime restart/stop, "
            "propose workflow-config-*, provider-dev-chat-*, or runtime-* only as "
            "owner-approved/proposal-only actions. For creating work, prefer "
            "action=create-task with payload.title and optional "
            "payload.contract={behavior,verification,acceptance}; the operator must "
            "confirm before the action runs. Keep answers concise and evidence-oriented."
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


@dataclass(frozen=True)
class _ParsedClaudeStream:
    provider_session_id: str
    reply: str
    messages: list[HeadlessMessage]
    usage: dict[str, Any]
    is_error: bool = False
    error: str = ""


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
            content = msg.get("message", {}).get("content", [])
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


class _CodexRpcClient:
    def __init__(
        self,
        args: list[str],
        *,
        cwd: Path,
        timeout_s: float,
        permission_profile: str = "read_only",
    ) -> None:
        self.args = args
        self.cwd = cwd
        self.timeout_s = timeout_s
        self.permission_profile = normalize_permission_profile(permission_profile)
        self.write_policy = permission_profile_write_policy(self.permission_profile)
        self.process: subprocess.Popen[str] | None = None
        self._queue: queue.Queue[dict[str, Any]] = queue.Queue()
        self._next_id = 0
        self._reply_parts: list[str] = []
        self._completed_reply = ""
        self._usage: dict[str, Any] = {}
        self._stderr_parts: list[str] = []
        self.thread_id = ""

    def start(self) -> None:
        self.process = subprocess.Popen(
            self.args,
            cwd=str(self.cwd),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=dict(os.environ),
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

    def wait_turn(self, *, on_message: MessageCallback | None = None) -> tuple[str, dict[str, Any]]:
        deadline = time.monotonic() + self.timeout_s
        while True:
            item = self._get_until(deadline)
            if item is None:
                self._raise_if_process_exited()
                if time.monotonic() >= deadline:
                    break
                continue
            deadline = time.monotonic() + self.timeout_s
            method = str(item.get("method") or "")
            if "id" in item and method:
                self._handle_server_request(item)
                continue
            message = self._handle_notification(item)
            if message is not None and on_message is not None:
                on_message(message)
            if method.endswith("turn/completed") or method == "turn/completed":
                self._raise_if_turn_failed(item)
                reply = self._completed_reply or "".join(part for part in self._reply_parts if part)
                return reply.strip(), self._usage
        stderr = _codex_stderr_tail(self._stderr_parts)
        raise RuntimeError(_codex_timeout_error("turn", timeout_s=self.timeout_s, stderr=stderr, idle=True))

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
                return message
        if method.endswith("item/completed"):
            message = _codex_item_message(item, message_type="tool_result")
            if message is not None:
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
        if self.permission_profile == "read_only":
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
        return False


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
