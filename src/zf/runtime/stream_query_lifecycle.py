"""Owned stream turn cancellation and bounded stop, independent of provider content."""

from __future__ import annotations

import asyncio
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
import threading
import time
from typing import Any, Callable

from zf.runtime.stream_result_contract import DrainStatus, completion_status
from zf.runtime.transport import DispatchContext


STOP_TIMEOUT_S = 5.0


class StreamCleanupError(RuntimeError):
    """Owned process cleanup is unproven; replacement must remain forbidden."""


@dataclass
class ActiveStreamTurn:
    cancelled: threading.Event = field(default_factory=threading.Event)
    done: threading.Event = field(default_factory=threading.Event)
    owner: threading.Thread = field(default_factory=threading.current_thread)
    lock: Any = field(default_factory=threading.Lock)
    loop: asyncio.AbstractEventLoop | None = None
    task: asyncio.Task | None = None

    def cancel(self) -> None:
        with self.lock:
            if self.cancelled.is_set():
                return  # Do not cancel a second time during SDK cleanup.
            self.cancelled.set()
            if self.loop is not None and self.task is not None:
                self.loop.call_soon_threadsafe(self.task.cancel)


async def drain_messages(
    query: Callable[..., Any], *, prompt: str, options: Any, timeout_s: float,
    turn: ActiveStreamTurn | None,
) -> tuple[list[Any], DrainStatus]:
    collected: list[Any] = []
    status = DrainStatus.OK
    if turn is not None:
        with turn.lock:
            if turn.cancelled.is_set():
                return [], DrainStatus.CANCELLED
            turn.loop, turn.task = asyncio.get_running_loop(), asyncio.current_task()
    try:
        async with asyncio.timeout(timeout_s):
            async for message in query(prompt=prompt, options=options):
                collected.append(message)
    except asyncio.CancelledError:
        if turn is None or not turn.cancelled.is_set():
            raise
        status = DrainStatus.CANCELLED
    except asyncio.TimeoutError:
        status = DrainStatus.TIMEOUT
    except Exception as exc:
        if "rate_limit_event" not in str(exc):
            raise
        status = DrainStatus.RATE_LIMITED
    finally:
        if turn is not None:
            with turn.lock:
                turn.loop = turn.task = None
    if turn is not None and turn.cancelled.is_set():
        return [], DrainStatus.CANCELLED
    return collected, completion_status(collected, status)


class StreamQueryLifecycleMixin:
    def _init_query_lifecycle(self) -> None:
        self._active_turns: dict[str, ActiveStreamTurn] = {}
        self._stopped_roles: set[str] = set()
        self._stopping_roles: Counter[str] = Counter()
        self._cleanup_failures: dict[str, str] = {}

    def _check_stream_dispatch(self, role_name: str) -> None:
        if self._closing or role_name in self._stopped_roles:
            raise RuntimeError(f"stream-json dispatch cancelled for stopped role {role_name!r}")

    def _prepare_stream_spawn(self, role_name: str) -> None:
        if role_name in self._cleanup_failures:
            raise StreamCleanupError(self._cleanup_failures[role_name])
        if self._closing or role_name in self._active_turns or self._stopping_roles[role_name]:
            raise RuntimeError(f"stream-json role {role_name!r} has not stopped")
        worker = self._dispatch_threads.get(role_name)
        if worker is not None and worker.is_alive():
            raise RuntimeError(f"stream-json role {role_name!r} still has a dispatch thread")
        self._stopped_roles.discard(role_name)

    def _send_task_sync(
        self, role_name: str, briefing_path: Path, prompt: str, *,
        context: DispatchContext | None = None,
    ) -> None:
        with self._dispatch_lock:
            self._check_stream_dispatch(role_name)
            if role_name in self._active_turns:
                raise RuntimeError(f"stream-json role {role_name!r} already has an active turn")
            turn = self._active_turns[role_name] = ActiveStreamTurn()
        try:
            self._send_task_turn(
                role_name, briefing_path, prompt, context=context, turn=turn,
            )
        except StreamCleanupError as exc:
            with self._dispatch_lock:
                self._cleanup_failures[role_name] = str(exc)
                self._stopped_roles.add(role_name)
            raise
        finally:
            with self._dispatch_lock:
                self._active_turns.pop(role_name, None)
                turn.done.set()

    def _stop_stream_roles(self, roles: set[str]) -> None:
        with self._dispatch_lock:
            self._stopping_roles.update(roles)
        try:
            self._cancel_and_join_stream_roles(roles)
        finally:
            with self._dispatch_lock:
                self._stopping_roles.subtract(roles)

    def _cancel_and_join_stream_roles(self, roles: set[str]) -> None:
        with self._dispatch_lock:
            self._stopped_roles.update(roles)
            turns = [self._active_turns[name] for name in roles if name in self._active_turns]
            workers = [self._dispatch_threads[name] for name in roles
                       if name in self._dispatch_threads]
            for name in roles:
                self._dispatch_queues.pop(name, None)
                self._last_query_ok[name] = False
            for turn in turns:
                turn.cancel()
        deadline = time.monotonic() + STOP_TIMEOUT_S
        for turn in turns:
            if turn.owner is threading.current_thread() or not turn.done.wait(
                max(0, deadline - time.monotonic()),
            ):
                raise RuntimeError("stream-json cancellation did not settle; replacement forbidden")
        for worker in workers:
            if worker is threading.current_thread():
                raise RuntimeError("stream-json cannot join its own dispatch thread")
            if worker.ident is not None:
                worker.join(max(0, deadline - time.monotonic()))
                if worker.is_alive():
                    raise RuntimeError("stream-json dispatch thread did not stop")
        with self._dispatch_lock:
            for name in roles:
                if name in self._cleanup_failures:
                    raise StreamCleanupError(self._cleanup_failures[name])
            for name in roles:
                for mapping in (self._messages, self._roles, self._cwd_by_role,
                                self._env_by_role, self._last_query_ok, self._heartbeat):
                    mapping.pop(name, None)
                self._session_used.discard(name)
