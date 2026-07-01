"""B-1203-02: agent.usage events must carry backend in payload so
consumers reading events.jsonl (like mixed_phase_report) can split by
backend without a second lookup against role_sessions.yaml.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from zf.core.events.model import ZfEvent


def test_stream_json_agent_usage_has_backend_field():
    from zf.runtime.transport_stream_json import StreamJsonTransport

    # Need a class named ResultMessage so cls == "ResultMessage" matches.
    class ResultMessage(SimpleNamespace):
        pass

    msg = ResultMessage(
        session_id="abc", total_cost_usd=0.01,
        usage={"input_tokens": 10, "output_tokens": 5},
        num_turns=1, duration_ms=1000, is_error=False,
    )
    events = StreamJsonTransport._messages_to_events("orchestrator-1", [msg])
    usage_evts = [e for e in events if e.type == "agent.usage"]
    assert usage_evts, "ResultMessage should produce agent.usage event"
    assert usage_evts[0].payload.get("backend") == "claude-code", (
        f"stream-json agent.usage must tag backend=claude-code, got "
        f"{usage_evts[0].payload.get('backend')!r}"
    )


def test_synthesized_agent_usage_has_backend_field(tmp_path: Path):
    """Disk-reader-synthesized agent.usage (orchestrator_lifecycle)
    must include backend from the role config."""
    from zf.core.config.schema import RoleConfig
    from zf.core.events.log import EventLog
    from zf.runtime.orchestrator_lifecycle import LifecycleManagerMixin

    # Minimal host object that satisfies _synthesize_agent_usage
    class _Host(LifecycleManagerMixin):
        def __init__(self, log: EventLog):
            self.event_log = log
            self._synth_usage_seen: set = set()

    log = EventLog(tmp_path / "events.jsonl")
    host = _Host(log)

    role = RoleConfig(name="dev", backend="codex", instance_id="dev-1")
    usage = SimpleNamespace(
        timestamp=1234,
        raw={"input_tokens": 100, "output_tokens": 50},
        effective_input_tokens=100,
        output_tokens=50,
        ratio=0.1,
        model_context_window=200000,
        model="claude-opus-4-8",
    )
    host._synthesize_agent_usage(role, usage)  # type: ignore[attr-defined]

    usage_evts = [e for e in log.read_all() if e.type == "agent.usage"]
    assert usage_evts, "synthesize should emit agent.usage"
    assert usage_evts[0].payload.get("backend") == "codex"


def test_synthesized_agent_usage_has_active_task_id(tmp_path: Path):
    from zf.core.config.schema import RoleConfig
    from zf.core.events.log import EventLog
    from zf.core.task.schema import Task
    from zf.core.task.store import TaskStore
    from zf.runtime.orchestrator_lifecycle import LifecycleManagerMixin

    class _Host(LifecycleManagerMixin):
        def __init__(self, log: EventLog, task_store: TaskStore):
            self.event_log = log
            self.task_store = task_store
            self._synth_usage_seen: set = set()

    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    task_store = TaskStore(state_dir / "kanban.json")
    task_store.add(Task(
        id="TASK-1",
        title="active",
        status="in_progress",
        assigned_to="dev-1",
    ))
    log = EventLog(state_dir / "events.jsonl")
    host = _Host(log, task_store)

    role = RoleConfig(name="dev", backend="codex", instance_id="dev-1")
    usage = SimpleNamespace(
        timestamp=1234,
        raw={"input_tokens": 100, "output_tokens": 50},
        effective_input_tokens=100,
        output_tokens=50,
        ratio=0.1,
        model_context_window=200000,
        model="claude-opus-4-8",
    )

    host._synthesize_agent_usage(role, usage)  # type: ignore[attr-defined]

    usage_evt = [e for e in log.read_all() if e.type == "agent.usage"][0]
    assert usage_evt.task_id == "TASK-1"
    assert usage_evt.payload.get("task_id") == "TASK-1"


def test_synthesized_agent_usage_prefers_latest_active_fanout_child(
    tmp_path: Path,
):
    from zf.core.config.schema import RoleConfig
    from zf.core.events.log import EventLog
    from zf.core.task.schema import Task
    from zf.core.task.store import TaskStore
    from zf.runtime.orchestrator_lifecycle import LifecycleManagerMixin

    class _Host(LifecycleManagerMixin):
        def __init__(self, log: EventLog, task_store: TaskStore):
            self.event_log = log
            self.task_store = task_store
            self._synth_usage_seen: set = set()

    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    task_store = TaskStore(state_dir / "kanban.json")
    task_store.add(Task(
        id="TASK-OLD",
        title="old",
        status="in_progress",
        assigned_to="dev-lane-1",
    ))
    task_store.add(Task(
        id="TASK-NEW",
        title="new",
        status="in_progress",
        assigned_to="dev-lane-1",
    ))
    log = EventLog(state_dir / "events.jsonl")
    log.append(ZfEvent(
        type="fanout.child.dispatched",
        actor="zf-cli",
        payload={
            "fanout_id": "fanout-1",
            "child_id": "queued-TASK-NEW-2",
            "run_id": "run-new",
            "role_instance": "dev-lane-1",
            "task_id": "TASK-NEW",
        },
    ))
    host = _Host(log, task_store)

    role = RoleConfig(name="dev", backend="codex", instance_id="dev-lane-1")
    usage = SimpleNamespace(
        timestamp=1234,
        raw={"input_tokens": 100, "output_tokens": 50},
        effective_input_tokens=100,
        output_tokens=50,
        ratio=0.1,
        model_context_window=200000,
        model="gpt-5.5-codex",
    )

    host._synthesize_agent_usage(role, usage)  # type: ignore[attr-defined]

    usage_evt = [e for e in log.read_all() if e.type == "agent.usage"][0]
    assert usage_evt.task_id == "TASK-NEW"
    assert usage_evt.payload.get("task_id") == "TASK-NEW"


def test_synthesized_agent_usage_ignores_terminal_fanout_child(
    tmp_path: Path,
):
    from zf.core.config.schema import RoleConfig
    from zf.core.events.log import EventLog
    from zf.core.task.schema import Task
    from zf.core.task.store import TaskStore
    from zf.runtime.orchestrator_lifecycle import LifecycleManagerMixin

    class _Host(LifecycleManagerMixin):
        def __init__(self, log: EventLog, task_store: TaskStore):
            self.event_log = log
            self.task_store = task_store
            self._synth_usage_seen: set = set()

    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    task_store = TaskStore(state_dir / "kanban.json")
    task_store.add(Task(
        id="TASK-OLD",
        title="old",
        status="in_progress",
        assigned_to="dev-lane-1",
    ))
    log = EventLog(state_dir / "events.jsonl")
    log.append(ZfEvent(
        type="fanout.child.dispatched",
        actor="zf-cli",
        payload={
            "fanout_id": "fanout-1",
            "child_id": "queued-TASK-OLD-1",
            "run_id": "run-old",
            "role_instance": "dev-lane-1",
            "task_id": "TASK-OLD",
        },
    ))
    log.append(ZfEvent(
        type="fanout.child.completed",
        actor="zf-cli",
        payload={
            "fanout_id": "fanout-1",
            "child_id": "queued-TASK-OLD-1",
            "run_id": "run-old",
            "role_instance": "dev-lane-1",
            "task_id": "TASK-OLD",
        },
    ))
    host = _Host(log, task_store)

    role = RoleConfig(name="dev", backend="codex", instance_id="dev-lane-1")
    usage = SimpleNamespace(
        timestamp=1234,
        raw={"input_tokens": 100, "output_tokens": 50},
        effective_input_tokens=100,
        output_tokens=50,
        ratio=0.1,
        model_context_window=200000,
        model="gpt-5.5-codex",
    )

    host._synthesize_agent_usage(role, usage)  # type: ignore[attr-defined]

    usage_evt = [e for e in log.read_all() if e.type == "agent.usage"][0]
    assert usage_evt.task_id is None
    assert usage_evt.payload.get("task_id") == ""


def test_mixed_phase_report_reads_payload_backend(tmp_path, capsys):
    """B-1203-02: with backend in payload, mixed_phase_report should
    show per-backend breakdown (not 'unknown')."""
    import json

    from tests.e2e.mixed_phase_report import print_mixed_report

    events_path = tmp_path / "events.jsonl"
    events_path.write_text("\n".join(json.dumps(e) for e in [
        {"type": "agent.usage", "actor": "dev-1",
         "payload": {"backend": "codex",
                     "usage": {"input_tokens": 100, "output_tokens": 50}}},
        {"type": "agent.usage", "actor": "orchestrator-1",
         "payload": {"backend": "claude-code",
                     "usage": {"input_tokens": 200, "output_tokens": 80}}},
    ]))
    print_mixed_report(events_path)
    out = capsys.readouterr().out
    assert "unknown" not in out.split("Mixed Backend Breakdown")[1].split(
        "codex.hook events")[0], \
        "With payload.backend set, breakdown must not fall back to 'unknown'"
    assert "codex" in out
    assert "claude-code" in out
