"""LH-3.T2/T3/T4: hook_recv defensive upgrades.

T2 — write-failure path:
  If EventLog.append raises (disk full / permission / corruption), the
  hook payload is written to .zf/hooks/dead_letter.jsonl so it's not
  lost, and a hook.write_failed event is queued for the NEXT successful
  append to carry into events.jsonl.

T3 — causation_id:
  When a hook fires for an actor whose latest task.dispatched event is
  known, the new event inherits that dispatched event's id as its
  causation_id. Missing → fall back to hook.orphan_event.

T4 — Tri-State (review.suspended / test.suspended):
  zf emit accepts the new event names; the orchestrator reactor turns
  them into a blocked task + human.escalate.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

from zf.cli.hook_recv import run as hook_run
from zf.core.config.schema import (
    ProjectConfig, RoleConfig, SessionConfig, ZfConfig,
)
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.state.role_sessions import RoleSessionRegistry
from zf.core.state.session import SessionStore
from zf.core.task.schema import Task
from zf.core.task.store import TaskStore
from zf.runtime.orchestrator import Orchestrator
from zf.runtime.tmux import TmuxSession
from zf.runtime.transport import TmuxTransport


@pytest.fixture
def state_dir(tmp_path: Path) -> Path:
    sd = tmp_path / ".zf"
    sd.mkdir()
    (sd / "memory").mkdir()
    EventLog(sd / "events.jsonl").append(
        ZfEvent(type="loop.started", actor="zf-cli")
    )
    SessionStore(sd / "session.yaml").create(project_root=str(tmp_path))
    (sd / "kanban.json").write_text("[]\n")
    return sd


def _invoke(state_dir: Path, event: str, stdin_payload: dict,
            monkeypatch) -> int:
    monkeypatch.setattr("sys.stdin", _StdinStub(json.dumps(stdin_payload)))
    args = argparse.Namespace(event=event, state_dir=str(state_dir))
    return hook_run(args)


class _StdinStub:
    def __init__(self, payload: str):
        self._payload = payload

    def read(self) -> str:
        return self._payload


class TestDeadLetterOnWriteFail:
    def test_write_failure_routes_to_dead_letter(
        self, state_dir, monkeypatch
    ):
        # Make EventLog.append raise by monkey-patching the class method
        from zf.core.events import log as log_module

        def boom(self, evt):
            raise OSError("disk full")

        monkeypatch.setattr(log_module.EventLog, "append", boom)

        rc = _invoke(state_dir, "claude.hook.post_tool_use",
                     {"session_id": "nope", "hook_event_name": "PostToolUse",
                      "tool_name": "Bash", "tool_input": {"cmd": "ls"}},
                     monkeypatch)
        assert rc == 0  # hook must never fail Claude turn

        dead = state_dir / "hooks" / "dead_letter.jsonl"
        assert dead.exists(), "payload lost — dead_letter missing"
        line = dead.read_text().splitlines()[0]
        data = json.loads(line)
        assert data.get("event_type") == "claude.hook.post_tool_use"

    def test_next_success_flushes_write_failed_alert(
        self, state_dir, monkeypatch
    ):
        """After a write failure, the next successful hook append must
        carry a hook.write_failed breadcrumb so the orchestrator sees
        the outage."""
        from zf.core.events import log as log_module

        call_count = {"n": 0}
        original = log_module.EventLog.append

        def sometimes_fail(self, evt):
            call_count["n"] += 1
            # First call from hook #1 fails; all subsequent calls succeed.
            if call_count["n"] == 1:
                raise OSError("disk full")
            return original(self, evt)

        monkeypatch.setattr(log_module.EventLog, "append", sometimes_fail)

        # First invocation — write fails, dead_letter gets the payload
        _invoke(state_dir, "claude.hook.stop",
                {"session_id": "x", "hook_event_name": "Stop"},
                monkeypatch)
        # Second invocation — should succeed AND flush a hook.write_failed
        _invoke(state_dir, "claude.hook.stop",
                {"session_id": "y", "hook_event_name": "Stop"},
                monkeypatch)

        # Restore append so we can read.
        monkeypatch.setattr(log_module.EventLog, "append", original)
        events = EventLog(state_dir / "events.jsonl").read_all()
        assert any(e.type == "hook.write_failed" for e in events)


class TestCausationId:
    def test_hook_carries_causation_from_dispatch(
        self, state_dir, monkeypatch
    ):
        # Seed a task + a task.dispatched event + a role session mapping.
        TaskStore(state_dir / "kanban.json").add(Task(
            id="T1", title="x", status="in_progress", assigned_to="dev",
        ))
        log = EventLog(state_dir / "events.jsonl")
        log.append(ZfEvent(
            type="task.dispatched", actor="orchestrator", task_id="T1",
            payload={"role": "dev", "assignee": "dev"},
        ))
        indexed = log.index.latest_dispatch_event_for_actor("dev")
        assert indexed is not None
        assert indexed.actor == "orchestrator"
        reg = RoleSessionRegistry(
            state_dir / "role_sessions.yaml",
            project_root=str(state_dir.parent),
        )
        reg.get_or_create("dev")
        uuid = reg.get("dev")

        _invoke(state_dir, "claude.hook.post_tool_use",
                {"session_id": str(uuid),
                 "hook_event_name": "PostToolUse",
                 "tool_name": "Write"},
                monkeypatch)

        events = log.read_all()
        dispatched = next(e for e in events if e.type == "task.dispatched")
        hook_evt = next(e for e in events
                        if e.type == "claude.hook.post_tool_use")
        assert hook_evt.causation_id == dispatched.id
        assert hook_evt.actor == "dev"

    def test_hook_causation_survives_codex_hook_flood(
        self, state_dir, monkeypatch
    ):
        TaskStore(state_dir / "kanban.json").add(Task(
            id="T1", title="x", status="in_progress", assigned_to="dev",
        ))
        log = EventLog(state_dir / "events.jsonl")
        log.append(ZfEvent(
            type="task.dispatched", actor="orchestrator", task_id="T1",
            payload={"role": "dev", "assignee": "dev"},
        ))
        dispatched = next(e for e in log.read_all() if e.type == "task.dispatched")
        assert log.index.latest_dispatch_event_for_actor("dev").id == dispatched.id
        for i in range(700):
            log.append(ZfEvent(
                type="codex.hook.post_tool_use",
                actor="dev",
                payload={"i": i},
            ))
        reg = RoleSessionRegistry(
            state_dir / "role_sessions.yaml",
            project_root=str(state_dir.parent),
        )
        reg.get_or_create("dev")
        uuid = reg.get("dev")

        _invoke(state_dir, "codex.hook.post_tool_use",
                {"session_id": str(uuid),
                 "hook_event_name": "PostToolUse",
                 "tool_name": "Read"},
                monkeypatch)

        events = log.read_all()
        hook_evt = events[-1]
        assert hook_evt.type == "codex.hook.post_tool_use"
        assert hook_evt.actor == "dev"
        assert hook_evt.causation_id == dispatched.id
        assert not any(e.type == "hook.orphan_event" for e in events)

    def test_hook_carries_causation_from_active_fanout_child(
        self, state_dir, monkeypatch
    ):
        log = EventLog(state_dir / "events.jsonl")
        log.append(ZfEvent(
            type="fanout.child.dispatched",
            actor="zf-cli",
            payload={
                "fanout_id": "fanout-scan-1",
                "trace_id": "trace-scan",
                "stage_id": "scan",
                "child_id": "scan-contract",
                "run_id": "run-fanout-scan-1-scan-contract",
                "role_instance": "scan-contract",
            },
            correlation_id="trace-scan",
        ))
        dispatched = next(
            event for event in log.read_all()
            if event.type == "fanout.child.dispatched"
        )
        indexed = log.index.latest_dispatch_event_for_actor("scan-contract")
        assert indexed is not None
        assert indexed.id == dispatched.id
        reg = RoleSessionRegistry(
            state_dir / "role_sessions.yaml",
            project_root=str(state_dir.parent),
        )
        reg.get_or_create("scan-contract")
        uuid = reg.get("scan-contract")

        _invoke(state_dir, "claude.hook.post_tool_use",
                {"session_id": str(uuid),
                 "hook_event_name": "PostToolUse",
                 "tool_name": "Read"},
                monkeypatch)

        events = log.read_all()
        hook_evt = next(
            event for event in events
            if event.type == "claude.hook.post_tool_use"
        )
        assert hook_evt.actor == "scan-contract"
        assert hook_evt.causation_id == dispatched.id
        assert not any(event.type == "hook.orphan_event" for event in events)

    def test_unresolved_session_emits_orphan_event(
        self, state_dir, monkeypatch
    ):
        _invoke(state_dir, "claude.hook.stop",
                {"session_id": "totally-unknown",
                 "hook_event_name": "Stop"},
                monkeypatch)
        events = EventLog(state_dir / "events.jsonl").read_all()
        assert any(e.type == "hook.orphan_event" for e in events)

    def test_terminal_run_quiesces_provider_stop_and_orphan_tail_noise(
        self, state_dir, monkeypatch
    ):
        log = EventLog(state_dir / "events.jsonl")
        log.append(ZfEvent(
            type="run.completed",
            actor="run-manager",
            payload={"status": "passed"},
        ))

        _invoke(state_dir, "provider.stop.check",
                {"session_id": "totally-unknown",
                 "hook_event_name": "Stop"},
                monkeypatch)
        _invoke(state_dir, "claude.hook.stop",
                {"session_id": "still-unknown",
                 "hook_event_name": "Stop"},
                monkeypatch)

        events = EventLog(state_dir / "events.jsonl").read_all()
        assert not any(e.type == "provider.stop.check" for e in events)
        assert any(e.type == "claude.hook.stop" for e in events)
        assert not any(e.type == "hook.orphan_event" for e in events)


class TestSuspendRouting:
    def _layer1_config(self):
        return ZfConfig(
            project=ProjectConfig(name="t"),
            session=SessionConfig(tmux_session="t"),
            roles=[
                RoleConfig(name="dev", backend="mock"),
                RoleConfig(name="review", backend="mock"),
            ],
        )

    def test_review_suspended_blocks_task_and_escalates(
        self, state_dir, monkeypatch
    ):
        store = TaskStore(state_dir / "kanban.json")
        store.add(Task(
            id="T1", title="x", status="review", assigned_to="dev",
        ))
        EventLog(state_dir / "events.jsonl").append(ZfEvent(
            type="review.suspended", actor="review", task_id="T1",
            payload={"reason": "missing_info"},
        ))
        transport = TmuxTransport(TmuxSession(session_name="t", dry_run=True))
        orch = Orchestrator(state_dir, self._layer1_config(), transport)
        orch.run_once()

        task = store.get("T1")
        assert task.status == "blocked"
        assert "missing_info" in (task.blocked_reason or "")
        events = EventLog(state_dir / "events.jsonl").read_all()
        assert any(e.type == "human.escalate" for e in events)

    def test_test_suspended_also_blocks(self, state_dir, monkeypatch):
        store = TaskStore(state_dir / "kanban.json")
        store.add(Task(
            id="T1", title="x", status="testing", assigned_to="dev",
        ))
        EventLog(state_dir / "events.jsonl").append(ZfEvent(
            type="test.suspended", actor="test", task_id="T1",
            payload={"reason": "env_broken"},
        ))
        transport = TmuxTransport(TmuxSession(session_name="t", dry_run=True))
        orch = Orchestrator(state_dir, self._layer1_config(), transport)
        orch.run_once()

        assert store.get("T1").status == "blocked"


class TestWireUp:
    def test_dead_letter_path_exists_in_hook_recv(self):
        src = (Path(__file__).resolve().parents[1]
               / "src/zf/cli/hook_recv.py").read_text()
        assert "dead_letter" in src

    def test_reactor_handles_suspended(self):
        src = (Path(__file__).resolve().parents[1]
               / "src/zf/runtime/orchestrator_reactor.py").read_text()
        assert "review.suspended" in src or "suspended" in src
