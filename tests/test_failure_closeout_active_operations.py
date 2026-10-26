"""Ancillary failure backlog promotion must not cancel in-flight workflow work."""
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from zf.core.config.schema import ProjectConfig, SessionConfig, ZfConfig
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.runtime.run_manager import build_run_manager_projection, run_manager_tick


def _fixture(tmp_path: Path, *, operation_run: str = "current", terminal: str = ""):
    state = tmp_path / ".runtime"
    state.mkdir()
    log = EventLog(state / "events.jsonl")
    writer = EventWriter(log)
    now = datetime.now(timezone.utc)
    events = [
        ("run.goal.started", {"run_id": "current", "objective": "Repair the plan"}),
        ("workflow.operation.requested", {
            "operation_id": "planning-rework", "workflow_run_id": operation_run,
            "operation_type": "fanout_reader_child", "request_hash": "bound-request",
        }),
        ("workflow.operation.started", {
            "operation_id": "planning-rework", "workflow_run_id": operation_run,
            "role_instance": "planner", "active_attempt_id": "attempt-rework",
        }),
        ("failure.closeout.materialized", {
            "manifest_ref": str(state / "failure-manifest.json"), "materialized_count": 1,
        }),
    ]
    if terminal:
        events.append(("workflow.operation." + terminal, {
            "operation_id": "planning-rework", "workflow_run_id": operation_run,
        }))
    for index, (kind, payload) in enumerate(events):
        writer.append(ZfEvent(
            type=kind, actor="zf-runtime", payload=payload,
            ts=(now - timedelta(minutes=20 - index)).isoformat(),
        ))
    # A real long provider turn can emit nothing for over the 300-second grace.
    writer.append(ZfEvent(type="run.manager.tick.started", actor="run-manager", ts=now.isoformat()))
    config = ZfConfig(project=ProjectConfig(name="test"), session=SessionConfig(tmux_session="test"))
    return state, log, writer, config


def _closeout_actions(state, log, config, root):
    result = build_run_manager_projection(
        state, events=log.read_all(), config=config, project_root=root,
    )
    return [a for a in result["pending_actions"] if a["action"] == "failure-closeout-activate"]


def test_running_operation_defers_closeout_after_worker_silence(tmp_path):
    state, log, _, config = _fixture(tmp_path)
    assert not _closeout_actions(state, log, config, tmp_path)


@pytest.mark.parametrize("terminal", ["settled", "failed", "blocked", "cancelled", "superseded", "interrupted"])
def test_nonrunning_operation_keeps_closeout_owner_approval(tmp_path, terminal):
    state, log, _, config = _fixture(tmp_path, terminal=terminal)
    actions = _closeout_actions(state, log, config, tmp_path)
    assert len(actions) == 1
    assert actions[0]["policy_decision"]["decision"] == "needs_approval"


def test_other_run_operation_does_not_hide_current_closeout(tmp_path):
    state, log, _, config = _fixture(tmp_path, operation_run="other")
    assert _closeout_actions(state, log, config, tmp_path)


def test_tick_does_not_block_goal_for_closeout_during_rework(tmp_path):
    state, log, writer, config = _fixture(tmp_path)
    run_manager_tick(state_dir=state, writer=writer, config=config, project_root=tmp_path)
    assert not [e for e in log.read_all() if e.type == "run.goal.blocked"]
