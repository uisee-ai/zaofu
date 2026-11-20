"""Late usage is activity/accounting, not a new task dispatch."""

from datetime import datetime, timedelta, timezone
from pathlib import Path
import subprocess

import pytest

from zf.core.config.schema import ProjectConfig, RoleConfig, SessionConfig, ZfConfig
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.state.role_sessions import RoleSessionRegistry
from zf.core.task.schema import Task
from zf.runtime.orchestrator import Orchestrator
from zf.runtime.pane_probe import build_runtime_pane_probe, pane_probe_attention_items
from zf.runtime.tmux import TmuxSession
from zf.runtime.transport import TmuxTransport


@pytest.mark.parametrize("released_state", ["idle", "awaiting_review"])
@pytest.mark.parametrize("task_location", ["event", "payload"])
def test_explicit_late_usage_preserves_released_role(
    tmp_path: Path, released_state: str, task_location: str,
) -> None:
    state = tmp_path / "state"
    state.mkdir()
    (state / "memory").mkdir()
    config = ZfConfig(
        project=ProjectConfig(name="test"),
        session=SessionConfig(tmux_session="test"),
        roles=[RoleConfig(name="dev", backend="mock", stuck_threshold_seconds=60)],
    )
    orch = Orchestrator(state, config, TmuxTransport(TmuxSession("test", dry_run=True)))
    # A blocked child leaves the canonical task unfinished until its successor.
    orch.task_store.add(Task(id="T1", title="unfinished", status="in_progress", assigned_to="dev"))
    orch._apply_housekeeping(ZfEvent(
        type="worker.state.changed", actor="dev", task_id="T1", origin="kernel",
        payload={"instance_id": "dev", "from": "busy", "to": released_state,
                 "reason": "task result admitted", "task_id": "T1"},
    ))
    assert orch._last_worker_state["dev"] == released_state
    payload = {"backend": "codex", "usage": {"input_tokens": 100, "output_tokens": 10}}
    if task_location == "payload":
        payload["task_id"] = "T1"
    usage = ZfEvent(type="agent.usage", actor="dev",
                   task_id="T1" if task_location == "event" else None, payload=payload)
    EventLog(state / "events.jsonl").append(usage)
    orch._apply_housekeeping(usage)
    registry = RoleSessionRegistry(state / "role_sessions.yaml", project_root=str(tmp_path))
    _, heartbeat = registry.get_last_heartbeat("dev")
    assert heartbeat["state"] == released_state
    assert heartbeat["current_task_id"] == "T1"
    assert heartbeat["source"] == "agent.usage"
    assert (state / "cost.jsonl").read_text().strip()

    def tmux(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        output = "%1\tcodex\t/tmp/project\t0\n" if args[1] == "display-message" else "Task submitted; waiting for next instruction\n"
        return subprocess.CompletedProcess(args, 0, stdout=output, stderr="")

    probe = build_runtime_pane_probe(
        state, config=config, project_root=tmp_path,
        now=datetime.now(timezone.utc) + timedelta(minutes=10), runner=tmux,
    )
    assert not pane_probe_attention_items(probe)


@pytest.mark.parametrize("next_task", ["T1", "T2"])
def test_real_dispatch_reactivates_role_after_release(tmp_path: Path, next_task: str) -> None:
    from zf.runtime.housekeeping import apply_task_dispatched_heartbeat_seed, apply_worker_state_changed_event
    from zf.runtime.usage_liveness import apply_agent_usage_liveness

    registry = RoleSessionRegistry(tmp_path / "role_sessions.yaml", project_root=str(tmp_path))
    apply_worker_state_changed_event(registry, ZfEvent(
        type="worker.state.changed", actor="dev", task_id="T1",
        payload={"instance_id": "dev", "from": "busy", "to": "idle", "task_id": "T1"},
    ))
    apply_task_dispatched_heartbeat_seed(registry, ZfEvent(
        type="task.dispatched", actor="zf-cli", task_id=next_task,
        payload={"instance_id": "dev", "task_id": next_task, "dispatch_id": "new-dispatch"},
    ))
    apply_agent_usage_liveness(registry, ZfEvent(
        type="agent.usage", actor="dev", task_id=next_task,
        payload={"context_usage_ratio": 0.3},
    ))
    _, heartbeat = registry.get_last_heartbeat("dev")
    assert heartbeat["state"] == "busy"
    assert heartbeat["current_task_id"] == next_task
