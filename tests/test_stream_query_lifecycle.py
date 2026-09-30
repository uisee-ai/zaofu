"""Cancellation races at the real stream dispatch entry, without a provider."""

import asyncio
from pathlib import Path
import threading
import time

import pytest

from tests.test_transport_stream_json import FakeResultMessage
from zf.core.config.schema import RoleConfig
from zf.core.state.role_sessions import RoleSessionRegistry
from zf.runtime.transport_stream_json import StreamJsonTransport


@pytest.fixture(autouse=True)
def isolated_provider_boundary(tmp_path, monkeypatch):
    home = tmp_path / "provider-home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(home / "config"))
    # Cancellation unit tests do not inspect host processes or user provider locks.
    # The separate real-SDK subprocess tests retain that production boundary.
    monkeypatch.setattr(
        "zf.runtime.transport_stream_json.purge_stale_claude_session_lock", lambda _sid: {},
    )


def _transport(tmp_path, query, *, background=True):
    state = tmp_path / "state"
    transport = StreamJsonTransport(
        state, RoleSessionRegistry(state / "role_sessions.yaml", str(tmp_path)),
        query_fn=query, background_dispatch=background,
    )
    transport.spawn(RoleConfig(name="dev", backend="mock"), argv=[])
    return transport


def _wait(predicate):
    deadline = time.monotonic() + 2
    while not predicate() and time.monotonic() < deadline:
        time.sleep(0.005)
    assert predicate(), "bounded synchronization failed"


@pytest.mark.parametrize("background", [False, True])
def test_failed_process_cleanup_forbids_stop_success_and_replacement(tmp_path, background):
    from zf.runtime.stream_query_lifecycle import StreamCleanupError

    async def query(*, prompt, options):
        raise StreamCleanupError("owned group did not stop")
        yield  # Keep the SDK async-iterator shape.

    transport = _transport(tmp_path, query, background=background)
    if background:
        transport.send_task("dev", Path("briefing.md"), "old")
        _wait(lambda: not transport._dispatch_threads)
        assert [e.type for e in transport.poll_events()] == ["agent.api_blocked"]
    else:
        with pytest.raises(StreamCleanupError):
            transport.send_task("dev", Path("briefing.md"), "old")
    assert not transport.is_alive("dev")
    for stop in (lambda: transport.terminate("dev"), transport.shutdown):
        with pytest.raises(StreamCleanupError, match="did not stop"):
            stop()
    transport.init()
    with pytest.raises(StreamCleanupError, match="did not stop"):
        transport.spawn(RoleConfig(name="dev", backend="mock"), argv=[])
    with pytest.raises(RuntimeError, match="cancelled"):
        transport.send_task("dev", Path("briefing.md"), "replacement")


@pytest.mark.parametrize("background", [False, True])
@pytest.mark.parametrize("action", ["terminate", "shutdown"])
def test_stop_suppresses_cancel_swallowing_query_and_allows_prepared_retry(
    tmp_path, background, action,
):
    started = threading.Event()
    cancelled = threading.Event()
    calls = []

    async def query(*, prompt, options):
        calls.append(prompt)
        if prompt == "old":
            started.set()
            try:
                await asyncio.sleep(10)
            except asyncio.CancelledError:
                cancelled.set()
        yield FakeResultMessage()  # Deliberately attempts late success after cancel.

    transport = _transport(tmp_path, query, background=background)
    errors = []

    def send_old():
        try:
            transport.send_task("dev", Path("briefing.md"), "old")
        except Exception as exc:
            errors.append(exc)

    sender = threading.Thread(target=send_old)
    sender.start()
    try:
        assert started.wait(5), errors  # Startup budget, not the cancellation deadline.
        if background:
            transport.send_task("dev", Path("queued.md"), "queued")
        if action == "terminate":
            transport.terminate("dev")
        else:
            transport.shutdown()
        sender.join(2)
        assert not sender.is_alive() and not errors
        assert cancelled.is_set()
        assert calls == ["old"]
        assert not transport.is_alive("dev")
        assert not transport._active_turns and not transport._dispatch_threads
        events = transport.poll_events()
        assert [event.type for event in events] == ["agent.api_blocked"]
        assert events[0].payload["drain_status"] == "cancelled"
        with pytest.raises(RuntimeError, match="cancelled"):
            transport.send_task("dev", Path("briefing.md"), "unprepared")
        transport.init()
        transport.spawn(RoleConfig(name="dev", backend="mock"), argv=[])
        transport.send_task("dev", Path("briefing.md"), "new")
        _wait(lambda: not transport._active_turns and not transport._dispatch_threads)
        assert transport.is_alive("dev")
        assert calls == ["old", "new"]
        assert [event.type for event in transport.poll_events()] == ["agent.usage"]
    finally:
        transport.shutdown()
        sender.join(2)
        assert not sender.is_alive()


def test_stop_before_async_loop_registration_does_not_launch_query(tmp_path, monkeypatch):
    entered = threading.Event()
    release = threading.Event()
    calls = []

    async def query(*, prompt, options):
        calls.append(prompt)
        yield FakeResultMessage()

    transport = _transport(tmp_path, query)
    build_options = transport._build_options

    def delayed_options(*args, **kwargs):
        entered.set()
        assert release.wait(2)
        return build_options(*args, **kwargs)

    monkeypatch.setattr(transport, "_build_options", delayed_options)
    transport.send_task("dev", Path("briefing.md"), "old")
    assert entered.wait(2)
    errors = []

    def stop():
        try:
            transport.terminate("dev")
        except Exception as exc:
            errors.append(exc)

    stopper = threading.Thread(target=stop)
    stopper.start()
    try:
        _wait(lambda: "dev" in transport._stopped_roles)
        with pytest.raises(RuntimeError, match="not stopped"):
            transport.spawn(RoleConfig(name="dev"), argv=[])
        release.set()
        stopper.join(2)
        assert not stopper.is_alive() and not errors
        assert calls == []
        [blocked] = transport.poll_events()
        assert blocked.payload["drain_status"] == "cancelled"
        assert not transport.is_alive("dev")
    finally:
        release.set()
        stopper.join(2)
        transport.shutdown()


def test_unsettled_cancellation_fails_closed_until_old_turn_finishes(tmp_path, monkeypatch):
    started = threading.Event()
    release = threading.Event()

    async def query(*, prompt, options):
        started.set()
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            while not release.is_set():
                await asyncio.sleep(0.005)
        yield FakeResultMessage()

    transport = _transport(tmp_path, query)
    transport.send_task("dev", Path("briefing.md"), "old")
    assert started.wait(5)
    monkeypatch.setattr("zf.runtime.stream_query_lifecycle.STOP_TIMEOUT_S", 0.02)
    try:
        with pytest.raises(RuntimeError, match="replacement forbidden"):
            transport.terminate("dev")
        assert not transport.is_alive("dev")
        with pytest.raises(RuntimeError, match="not stopped"):
            transport.spawn(RoleConfig(name="dev"), argv=[])
        release.set()
        _wait(lambda: not transport._active_turns and not transport._dispatch_threads)
        [blocked] = transport.poll_events()
        assert blocked.payload["drain_status"] == "cancelled"
        assert not transport.is_alive("dev")
        transport.terminate("dev")  # Explicit retry is idempotent after settlement.
    finally:
        release.set()
        _wait(lambda: not transport._active_turns and not transport._dispatch_threads)
        transport.shutdown()


def test_stopping_one_role_does_not_cancel_another(tmp_path):
    started = threading.Event()

    async def query(*, prompt, options):
        if prompt == "old":
            started.set()
            await asyncio.sleep(10)
        yield FakeResultMessage()

    transport = _transport(tmp_path, query)
    transport.spawn(RoleConfig(name="review", backend="mock"), argv=[])
    try:
        transport.send_task("dev", Path("briefing.md"), "old")
        assert started.wait(5)
        transport.terminate("dev")
        assert transport.is_alive("review")
        transport.send_task("review", Path("briefing.md"), "review")
        _wait(lambda: not transport._active_turns and not transport._dispatch_threads)
        assert transport.is_alive("review") and not transport.is_alive("dev")
        events = transport.poll_events()
        assert [(event.actor, event.type) for event in events] == [
            ("dev", "agent.api_blocked"), ("review", "agent.usage"),
        ]
    finally:
        transport.shutdown()


def test_shutdown_clears_completed_direct_dispatch_without_registration(tmp_path):
    async def query(*, prompt, options):
        yield FakeResultMessage()

    transport = _transport(tmp_path, query, background=False)
    transport.send_task("direct", Path("briefing.md"), "direct")
    assert transport.is_alive("direct")
    transport.shutdown()
    assert not transport.is_alive("direct")
    with pytest.raises(RuntimeError, match="cancelled"):
        transport.send_task("direct", Path("briefing.md"), "late")
