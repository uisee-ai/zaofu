"""In-process runtime registry for Web agent-session provider processes."""

from __future__ import annotations

import subprocess
import threading
from dataclasses import dataclass
from datetime import datetime, timezone


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class AgentSessionRunKey:
    run_id: str
    thread_id: str
    project_id: str = ""
    conversation_id: str = ""

    def normalized(self) -> "AgentSessionRunKey":
        return AgentSessionRunKey(
            run_id=self.run_id.strip(),
            thread_id=self.thread_id.strip(),
            project_id=self.project_id.strip(),
            conversation_id=self.conversation_id.strip(),
        )


@dataclass(frozen=True)
class AgentSessionCancelResult:
    status: str
    interrupt_supported: bool
    process_found: bool
    process_terminated: bool
    pid: int | None = None
    reason: str = ""


@dataclass
class _RunRecord:
    key: AgentSessionRunKey
    provider: str
    pid: int | None = None
    process: subprocess.Popen[str] | None = None
    cancel_requested: bool = False
    started_at: str = ""
    cancelled_at: str = ""


class AgentSessionRunRegistry:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._records: dict[tuple[str, str], _RunRecord] = {}

    def begin(self, key: AgentSessionRunKey, *, provider: str = "") -> None:
        key = key.normalized()
        if not key.run_id or not key.thread_id:
            return
        with self._lock:
            record = self._records.get(self._lookup(key))
            if record is None:
                self._records[self._lookup(key)] = _RunRecord(
                    key=key,
                    provider=provider,
                    started_at=_now(),
                )
            elif provider:
                record.provider = provider

    def register_process(
        self,
        key: AgentSessionRunKey,
        *,
        provider: str,
        process: subprocess.Popen[str],
    ) -> bool:
        key = key.normalized()
        if not key.run_id or not key.thread_id:
            return False
        with self._lock:
            record = self._records.setdefault(
                self._lookup(key),
                _RunRecord(key=key, provider=provider, started_at=_now()),
            )
            record.provider = provider
            record.pid = process.pid
            record.process = process
            cancel_requested = record.cancel_requested
        if cancel_requested:
            self._terminate(process)
        return cancel_requested

    def finish(self, key: AgentSessionRunKey) -> None:
        key = key.normalized()
        with self._lock:
            record = self._records.get(self._lookup(key))
            if record is not None:
                record.process = None

    def is_cancelled(self, key: AgentSessionRunKey) -> bool:
        key = key.normalized()
        with self._lock:
            return bool(self._records.get(self._lookup(key)) and self._records[self._lookup(key)].cancel_requested)

    def cancel(self, key: AgentSessionRunKey) -> AgentSessionCancelResult:
        key = key.normalized()
        if not key.run_id or not key.thread_id:
            return AgentSessionCancelResult(
                status="invalid",
                interrupt_supported=False,
                process_found=False,
                process_terminated=False,
                reason="run_id and thread_id are required",
            )
        with self._lock:
            record = self._records.setdefault(
                self._lookup(key),
                _RunRecord(key=key, provider="", started_at=_now()),
            )
            record.cancel_requested = True
            record.cancelled_at = _now()
            process = record.process
            pid = record.pid
        if process is None:
            return AgentSessionCancelResult(
                status="cancel_requested",
                interrupt_supported=False,
                process_found=False,
                process_terminated=False,
                pid=pid,
                reason="no active provider process is registered for this run",
            )
        if process.poll() is not None:
            return AgentSessionCancelResult(
                status="already_exited",
                interrupt_supported=True,
                process_found=True,
                process_terminated=False,
                pid=pid,
                reason="provider process already exited",
            )
        terminated = self._terminate(process)
        return AgentSessionCancelResult(
            status="interrupted" if terminated else "cancel_requested",
            interrupt_supported=True,
            process_found=True,
            process_terminated=terminated,
            pid=pid,
            reason="provider process terminated" if terminated else "provider process termination failed",
        )

    @staticmethod
    def _lookup(key: AgentSessionRunKey) -> tuple[str, str]:
        return (key.run_id, key.thread_id)

    @staticmethod
    def _terminate(process: subprocess.Popen[str]) -> bool:
        if process.poll() is not None:
            return False
        try:
            # SIGTERM → grace → SIGKILL. 5s grace (was 1s) lets an in-flight
            # provider subprocess — e.g. a coding agent mid-OAuth or flushing a
            # final message — exit cleanly before we hard-kill it.
            process.terminate()
            process.wait(timeout=5)
            return True
        except Exception:
            try:
                process.kill()
                process.wait(timeout=1)
                return True
            except Exception:
                return False


_REGISTRY = AgentSessionRunRegistry()


def begin_agent_session_run(key: AgentSessionRunKey, *, provider: str = "") -> None:
    _REGISTRY.begin(key, provider=provider)


def register_agent_session_process(
    key: AgentSessionRunKey,
    *,
    provider: str,
    process: subprocess.Popen[str],
) -> bool:
    return _REGISTRY.register_process(key, provider=provider, process=process)


def finish_agent_session_run(key: AgentSessionRunKey) -> None:
    _REGISTRY.finish(key)


def cancel_agent_session_run(key: AgentSessionRunKey) -> AgentSessionCancelResult:
    return _REGISTRY.cancel(key)


def agent_session_run_cancelled(key: AgentSessionRunKey) -> bool:
    return _REGISTRY.is_cancelled(key)


class agent_session_process:
    def __init__(
        self,
        key: AgentSessionRunKey,
        *,
        provider: str,
        process: subprocess.Popen[str],
    ) -> None:
        self.key = key
        self.provider = provider
        self.process = process

    def __enter__(self) -> bool:
        return register_agent_session_process(
            self.key,
            provider=self.provider,
            process=self.process,
        )

    def __exit__(self, *_exc: object) -> None:
        finish_agent_session_run(self.key)


def run_key(
    *,
    run_id: str = "",
    thread_id: str = "",
    project_id: str = "",
    conversation_id: str = "",
) -> AgentSessionRunKey:
    return AgentSessionRunKey(
        run_id=run_id,
        thread_id=thread_id,
        project_id=project_id,
        conversation_id=conversation_id,
    ).normalized()
