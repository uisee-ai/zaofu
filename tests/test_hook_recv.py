"""Phase 1 — zf hook-recv bridge Claude Code hooks into events.jsonl."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from zf.cli.hook_recv import run as hook_recv_run
from zf.core.events.log import EventLog
from zf.core.security.signing import EventSigner
from zf.core.state.role_sessions import RoleSessionRegistry


class _Args:
    def __init__(self, event: str, state_dir: str | None):
        self.event = event
        self.state_dir = state_dir
        self.backend = ""


@pytest.fixture
def state_dir(tmp_path: Path) -> Path:
    sd = tmp_path / ".zf"
    sd.mkdir()
    return sd


@pytest.fixture
def registry(state_dir: Path):
    r = RoleSessionRegistry(
        state_dir / "role_sessions.yaml", project_root=str(state_dir.parent),
    )
    # Seed two role uuids
    r.get_or_create("orchestrator")
    r.get_or_create("dev-1")
    return r


def _stub_stdin(monkeypatch, payload: dict):
    import io
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))


class TestHookRecvResolvesActor:
    def test_hook_emits_with_instance_id(
        self, state_dir, registry, monkeypatch
    ):
        # Use the real registered orchestrator uuid
        orch_uuid = str(registry.get("orchestrator"))
        _stub_stdin(monkeypatch, {
            "session_id": orch_uuid,
            "hook_event_name": "Stop",
        })
        args = _Args(event="orchestrator.round.complete", state_dir=str(state_dir))
        rc = hook_recv_run(args)
        assert rc == 0

        events = EventLog(state_dir / "events.jsonl").read_all()
        assert len(events) == 1
        assert events[0].type == "orchestrator.round.complete"
        assert events[0].actor == "orchestrator"
        assert events[0].payload["session_id"] == orch_uuid

    def test_unresolved_session_uses_fallback_actor(
        self, state_dir, registry, monkeypatch
    ):
        _stub_stdin(monkeypatch, {
            "session_id": "ffffffff-ffff-4fff-ffff-ffffffffffff",
        })
        args = _Args(event="orchestrator.round.complete", state_dir=str(state_dir))
        rc = hook_recv_run(args)
        assert rc == 0
        events = EventLog(state_dir / "events.jsonl").read_all()
        assert events[0].actor.startswith("unresolved:")

    def test_bad_stdin_is_non_fatal(self, state_dir, monkeypatch):
        import io
        monkeypatch.setattr("sys.stdin", io.StringIO("not json at all"))
        args = _Args(event="x.y", state_dir=str(state_dir))
        rc = hook_recv_run(args)
        # Hook errors MUST NOT block Claude — always exit 0
        assert rc == 0
        err_log = state_dir / "hooks" / "errors.log"
        assert err_log.exists()

    def test_pre_tool_use_payload_captured(
        self, state_dir, registry, monkeypatch
    ):
        orch_uuid = str(registry.get("orchestrator"))
        _stub_stdin(monkeypatch, {
            "session_id": orch_uuid,
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": "zf kanban add 'something'"},
        })
        args = _Args(event="agent.tool.use", state_dir=str(state_dir))
        hook_recv_run(args)
        events = EventLog(state_dir / "events.jsonl").read_all()
        assert events[0].payload["tool_name"] == "Bash"
        assert "zf kanban add" in events[0].payload["tool_input"]["command"]

    def test_default_state_dir_uses_project_config(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        state_dir = tmp_path / "runtime-state"
        state_dir.mkdir()
        (tmp_path / "zf.yaml").write_text(
            'version: "1.0"\nproject:\n  name: test\n  state_dir: runtime-state\n'
        )
        registry = RoleSessionRegistry(
            state_dir / "role_sessions.yaml",
            project_root=str(tmp_path),
        )
        orch_uuid = str(registry.get_or_create("orchestrator"))
        _stub_stdin(monkeypatch, {
            "session_id": orch_uuid,
            "hook_event_name": "Stop",
        })

        rc = hook_recv_run(_Args(event="orchestrator.round.complete", state_dir=None))

        assert rc == 0
        events = EventLog(state_dir / "events.jsonl").read_all()
        assert events[0].actor == "orchestrator"
        assert not (tmp_path / ".zf").exists()

    def test_explicit_state_dir_uses_project_root_for_session_seed(
        self, tmp_path, monkeypatch
    ):
        project_root = tmp_path / "project"
        state_dir = tmp_path / "runtime-state"
        project_root.mkdir()
        state_dir.mkdir()
        monkeypatch.chdir(project_root)
        (project_root / "zf.yaml").write_text(
            f'version: "1.0"\nproject:\n  name: test\n  state_dir: "{state_dir}"\n',
            encoding="utf-8",
        )
        registry = RoleSessionRegistry(
            state_dir / "role_sessions.yaml",
            project_root=str(project_root.resolve()),
        )
        orch_uuid = str(registry.get_or_create("orchestrator"))
        _stub_stdin(monkeypatch, {
            "session_id": orch_uuid,
            "hook_event_name": "Stop",
        })

        rc = hook_recv_run(
            _Args(
                event="orchestrator.round.complete",
                state_dir=str(state_dir),
            )
        )

        assert rc == 0
        events = EventLog(state_dir / "events.jsonl").read_all()
        assert events[0].actor == "orchestrator"

    def test_explicit_state_dir_still_loads_signing_config(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("ZF_EVENT_SECRET", "hook-secret")
        state_dir = tmp_path / "runtime-state"
        state_dir.mkdir()
        (tmp_path / "zf.yaml").write_text(
            'version: "1.0"\n'
            "project:\n"
            "  name: test\n"
            "  state_dir: runtime-state\n"
            "security:\n"
            "  event_signing:\n"
            "    enabled: true\n"
        )
        registry = RoleSessionRegistry(
            state_dir / "role_sessions.yaml",
            project_root=str(tmp_path),
        )
        orch_uuid = str(registry.get_or_create("orchestrator"))
        _stub_stdin(monkeypatch, {
            "session_id": orch_uuid,
            "hook_event_name": "Stop",
        })

        rc = hook_recv_run(
            _Args(
                event="orchestrator.round.complete",
                state_dir=str(state_dir),
            )
        )

        assert rc == 0
        raw = (state_dir / "events.jsonl").read_text().strip()
        assert '"event"' in raw and '"sig"' in raw
        events = EventLog(
            state_dir / "events.jsonl",
            signer=EventSigner(b"hook-secret"),
        ).read_all()
        assert events[0].type == "orchestrator.round.complete"


class TestRegistryReverseLookup:
    def test_get_instance_by_uuid_hits(self, registry):
        orch_uuid = str(registry.get("orchestrator"))
        assert registry.get_instance_by_uuid(orch_uuid) == "orchestrator"

    def test_get_instance_by_uuid_misses(self, registry):
        assert registry.get_instance_by_uuid("not-a-real-uuid") is None


# ---------------------------------------------------------------- B3 synth


def _evt(etype: str, eid: str, payload: dict) -> "ZfEvent":
    from zf.core.events.model import ZfEvent

    return ZfEvent(type=etype, actor="zf-cli", payload=payload, id=eid)


def test_synth_dispatch_resolves_as_active_context():
    # B3 (R25 ISSUE-005): fanout.synth.dispatched was unknown to the
    # causation resolver — every synth hook went orphan and the synth
    # looked dead for the whole 40min stage budget.
    from zf.cli.hook_recv import _latest_active_dispatch_for_actor

    events = [
        _evt("fanout.synth.dispatched", "evt-synth-1", {
            "fanout_id": "fanout-x",
            "role_instance": "review-synth",
        }),
    ]
    assert (
        _latest_active_dispatch_for_actor(events, "review-synth")
        == "evt-synth-1"
    )


def test_synth_dispatch_inactive_after_terminal():
    from zf.cli.hook_recv import (
        _latest_active_dispatch_for_actor,
        _synth_dispatch_is_active,
    )

    dispatch = _evt("fanout.synth.dispatched", "evt-synth-1", {
        "fanout_id": "fanout-x",
        "role_instance": "review-synth",
    })
    done = _evt("fanout.synth.completed", "evt-synth-2", {
        "fanout_id": "fanout-x",
        "status": "completed",
    })
    assert _latest_active_dispatch_for_actor(
        [dispatch, done], "review-synth",
    ) is None
    assert _synth_dispatch_is_active([dispatch, done], dispatch) is False
    assert _synth_dispatch_is_active([dispatch], dispatch) is True
