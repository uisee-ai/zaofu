"""Native compaction must cancel obsolete recycle intent and preserve task files."""

import subprocess

import pytest

from tests.test_context_threshold_detection import (
    _FakeReader, _make_usage, claude_config, state_dir, transport,
)
from zf.core.events.model import ZfEvent
from zf.core.task.schema import Task
from zf.runtime.orchestrator import Orchestrator


@pytest.mark.parametrize("active", ["task", "fanout", "idle"])
def test_fresh_low_context_cancels_pending_recycle(state_dir, claude_config, transport, active, monkeypatch):
    orch = Orchestrator(state_dir, claude_config, transport)
    if active == "task":
        orch.task_store.add(Task(id="T1", title="x", status="in_progress", assigned_to="dev"))
        orch._set_worker_state("dev", "busy", task_id="T1")
    elif active == "fanout":
        orch.event_writer.append(ZfEvent(type="fanout.child.dispatched", actor="kernel", payload={
            "fanout_id": "f1", "child_id": "reader", "run_id": "read-1", "role_instance": "dev",
        }))
    orch._instance_state["dev"] = "pending_recycle"
    orch._set_worker_state("dev", "pending_recycle")
    orch._session_readers = {"claude-code": _FakeReader(_make_usage(0.1))}
    recycled = []
    monkeypatch.setattr(orch, "_start_recycle", lambda role: recycled.append(role.instance_id))
    orch._check_context_thresholds()
    assert orch._instance_state["dev"] == "healthy"
    assert orch._last_worker_state["dev"] == ("idle" if active == "idle" else "busy")
    orch._check_pending_recycles()
    assert recycled == []
    transitions = [e for e in orch.event_log.read_all() if e.type == "worker.state.changed"]
    assert "context" in transitions[-1].payload["reason"]


@pytest.mark.parametrize("state,report,fresh", [
    ("recycling", _make_usage(0.1), True),
    ("pending_recycle", _make_usage(0.1), False),
    ("pending_recycle", None, True),
    ("pending_recycle", _make_usage(0.8), True),
])
def test_only_fresh_low_sample_cancels_pending(state_dir, claude_config, transport, monkeypatch, state, report, fresh):
    orch = Orchestrator(state_dir, claude_config, transport)
    orch.task_store.add(Task(id="T1", title="x", status="in_progress", assigned_to="dev"))
    orch._instance_state["dev"] = state
    orch._session_readers = {"claude-code": _FakeReader(report)}
    monkeypatch.setattr(orch, "_synthesize_agent_usage", lambda *_: fresh)
    orch._check_context_thresholds()
    assert orch._instance_state["dev"] == state


def test_context_growth_after_native_compact_can_request_compact_again(state_dir, claude_config, transport, monkeypatch):
    orch = Orchestrator(state_dir, claude_config, transport)
    orch.task_store.add(Task(id="T1", title="x", status="in_progress", assigned_to="dev"))
    reader = _FakeReader(_make_usage(0.1))
    orch._session_readers = {"claude-code": reader}
    orch._instance_state["dev"] = "pending_recycle"
    orch._context_compact_attempts().add("dev")
    orch._check_context_thresholds()
    assert "dev" not in orch._context_compact_attempts()
    reader._report = _make_usage(0.8, timestamp="2026-04-15T10:01:00Z")
    compact = []
    monkeypatch.setattr(orch, "_start_compact_first", lambda *args: compact.append(args) or True)
    orch._check_context_thresholds()
    assert len(compact) == 1


@pytest.mark.parametrize("terminal", [False, True])
def test_recovery_captures_live_task_workspace(state_dir, claude_config, transport, monkeypatch, terminal):
    orch = Orchestrator(state_dir, claude_config, transport)
    orch.task_store.add(Task(id="T1", title="x", status="in_progress", assigned_to="dev"))
    task_root = state_dir / "workdirs/tasks/run-1/T1/g1/project"
    task_root.mkdir(parents=True)
    for root, filename in [(state_dir.parent, "root-only.txt"), (task_root, "task-only.txt")]:
        subprocess.run(["git", "init", "-q", str(root)], check=True)
        subprocess.run(["git", "-C", str(root), "-c", "user.name=Test", "-c", "user.email=test@local",
                        "commit", "--allow-empty", "-qm", "init"], check=True)
        (root / filename).write_text("uncommitted work\n")
    orch.event_writer.append(ZfEvent(type="task.pipeline.stage.dispatched", actor="kernel", task_id="T1", payload={
        "role_instance": "dev", "attempt_id": "a1", "workdir": str(task_root),
    }))
    if terminal:
        orch.event_writer.append(ZfEvent(type="task.attempt.failed", actor="kernel", task_id="T1", payload={"attempt_id": "a1"}))
    captured = []

    def build(*args, **kwargs):
        captured.append(kwargs["git_context"])
        return "recovery\n"

    monkeypatch.setattr("zf.runtime.orchestrator_lifecycle.build_recovery_briefing", build)
    monkeypatch.setattr(orch, "_ensure_recovery_contract_sufficient", lambda *_: True)
    monkeypatch.setattr(orch, "_send_transport_task", lambda *_: None)
    assert orch._inject_recovery_briefing(claude_config.roles[0])
    assert ("task-only.txt" in captured[0].dirty_files) is not terminal
    assert ("root-only.txt" in captured[0].dirty_files) is terminal
