"""Tests for G-XPORT-1: StreamJsonTransport.is_alive caches last query state.

Previously is_alive() hardcoded False because stream-json has no long-lived
process. That broke any watchdog that asks "is this role still functional?"
— they get a false positive every time, firing escalations.

New semantics: is_alive returns True by default; flips to False when the
most recent send_task raised an exception; flips back to True on next
successful send_task.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from zf.core.config.schema import RoleConfig
from zf.core.state.role_sessions import RoleSessionRegistry
from zf.runtime.transport_stream_json import StreamJsonTransport


@pytest.fixture
def state_dir(tmp_path: Path) -> Path:
    sd = tmp_path / ".zf"
    sd.mkdir()
    (sd / "locks" / "sessions").mkdir(parents=True)
    return sd


@pytest.fixture
def registry(state_dir: Path) -> RoleSessionRegistry:
    return RoleSessionRegistry(state_dir / "role_sessions.yaml", project_root="/tmp/zf")


def _make_query_fn(*, raise_exc: Exception | None = None, messages=None):
    """Build a fake claude_code_sdk.query async-generator."""
    messages = messages or []

    async def _fake(prompt: str, options=None):
        if raise_exc is not None:
            raise raise_exc
        for m in messages:
            yield m

    return _fake


class TestIsAliveDefault:
    def test_is_alive_true_by_default(self, state_dir, registry):
        """A fresh transport (no query run yet) is optimistically alive."""
        t = StreamJsonTransport(state_dir, registry, query_fn=_make_query_fn())
        t.spawn(RoleConfig(name="dev"), argv=[])
        assert t.is_alive("dev") is True


class TestIsAliveAfterFailure:
    def test_is_alive_false_after_send_task_raises(
        self, state_dir, registry, tmp_path
    ):
        t = StreamJsonTransport(
            state_dir, registry,
            query_fn=_make_query_fn(raise_exc=RuntimeError("simulated SDK crash")),
        )
        t.spawn(RoleConfig(name="dev"), argv=[])
        briefing = tmp_path / "b.md"
        briefing.write_text("task")

        with pytest.raises(RuntimeError):
            t.send_task("dev", briefing, "go")

        assert t.is_alive("dev") is False

    def test_is_alive_per_role(self, state_dir, registry, tmp_path):
        """A dead dev does not taint a healthy review."""
        t = StreamJsonTransport(state_dir, registry, query_fn=_make_query_fn())
        t.spawn(RoleConfig(name="dev"), argv=[])
        t.spawn(RoleConfig(name="review"), argv=[])
        briefing = tmp_path / "b.md"
        briefing.write_text("task")

        # Successful call on review keeps it alive
        t.send_task("review", briefing, "ok")
        assert t.is_alive("review") is True

        # dev never had a query → still the default True (optimistic)
        assert t.is_alive("dev") is True

        # Now kill dev via a failing query
        t._query_fn = _make_query_fn(raise_exc=ValueError("boom"))
        with pytest.raises(ValueError):
            t.send_task("dev", briefing, "go")

        assert t.is_alive("dev") is False
        assert t.is_alive("review") is True  # unaffected


class TestIsAliveRecovery:
    def test_is_alive_recovers_after_successful_retry(
        self, state_dir, registry, tmp_path
    ):
        t = StreamJsonTransport(
            state_dir, registry,
            query_fn=_make_query_fn(raise_exc=RuntimeError("boom")),
        )
        t.spawn(RoleConfig(name="dev"), argv=[])
        briefing = tmp_path / "b.md"
        briefing.write_text("task")

        with pytest.raises(RuntimeError):
            t.send_task("dev", briefing, "go")
        assert not t.is_alive("dev")

        # Swap to a passing query
        t._query_fn = _make_query_fn()
        t.send_task("dev", briefing, "go")
        assert t.is_alive("dev") is True


class TestIsAliveUnknownRole:
    def test_unknown_role_is_alive_false(self, state_dir, registry):
        """Role that was never spawned should return False (no capacity)."""
        t = StreamJsonTransport(state_dir, registry, query_fn=_make_query_fn())
        assert t.is_alive("ghost") is False
