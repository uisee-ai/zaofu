"""Tests for G-XPORT-3: capture_log heartbeat for stream-json stuck parity.

The Orchestrator's StuckDetector hashes capture_log() output. For the
hash to change when the worker is alive, stream-json needs to surface
*something* that changes on each new message. tmux gets this for free
from the real pane (cursor, spinners, output). Stream-json must
manufacture a heartbeat line based on the most recent message timestamp.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from zf.core.config.schema import RoleConfig
from zf.core.state.role_sessions import RoleSessionRegistry
from zf.runtime.transport_stream_json import StreamJsonTransport


class _FakeTextBlock:
    def __init__(self, text: str):
        self.text = text


class _FakeAssistant:
    def __init__(self, text: str):
        self.content = [_FakeTextBlock(text)]


@pytest.fixture
def state_dir(tmp_path: Path) -> Path:
    sd = tmp_path / ".zf"
    sd.mkdir()
    (sd / "locks" / "sessions").mkdir(parents=True)
    return sd


@pytest.fixture
def registry(state_dir: Path) -> RoleSessionRegistry:
    return RoleSessionRegistry(state_dir / "role_sessions.yaml", project_root="/tmp/zf")


class TestCaptureLogHeartbeat:
    def test_capture_log_includes_heartbeat_line(self, state_dir, registry):
        t = StreamJsonTransport(state_dir, registry)
        t.spawn(RoleConfig(name="dev"), argv=[])
        out = t.capture_log("dev")
        assert "heartbeat:" in out

    def test_heartbeat_stable_when_no_new_messages(self, state_dir, registry):
        t = StreamJsonTransport(state_dir, registry)
        t.spawn(RoleConfig(name="dev"), argv=[])
        a = t.capture_log("dev")
        b = t.capture_log("dev")
        assert a == b

    def test_heartbeat_advances_when_new_message_arrives(
        self, state_dir, registry
    ):
        """Simulate a new message appended to _messages — the heartbeat
        timestamp should change so the stuck detector's hash also changes."""
        t = StreamJsonTransport(state_dir, registry)
        t.spawn(RoleConfig(name="dev"), argv=[])
        before = t.capture_log("dev")

        # Simulate a new drained message
        t._messages["dev"].append(_FakeAssistant("new progress"))
        t._bump_heartbeat("dev")  # public-ish hook for deterministic tests

        after = t.capture_log("dev")
        assert before != after

    def test_heartbeat_different_per_role(self, state_dir, registry):
        t = StreamJsonTransport(state_dir, registry)
        t.spawn(RoleConfig(name="dev"), argv=[])
        t.spawn(RoleConfig(name="review"), argv=[])

        # bump dev only
        t._messages["dev"].append(_FakeAssistant("dev progress"))
        t._bump_heartbeat("dev")

        out_dev = t.capture_log("dev")
        out_review = t.capture_log("review")
        assert "dev progress" in out_dev
        assert "dev progress" not in out_review
