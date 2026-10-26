from __future__ import annotations

from argparse import Namespace

import pytest

from zf.cli.goal import _SETTABLE_STATUSES, _run_set
from zf.core.config.schema import ProjectConfig, ZfConfig
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter


def test_goal_cli_accepts_limit_statuses() -> None:
    assert "usage_limited" in _SETTABLE_STATUSES
    assert "budget_limited" in _SETTABLE_STATUSES


def test_goal_cli_binds_active_update_to_current_run(tmp_path, monkeypatch) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    config = ZfConfig(project=ProjectConfig(name="goal-cli"))
    log = EventLog(state_dir / "events.jsonl")
    EventWriter(log).append(ZfEvent(
        type="run.goal.started",
        actor="orchestrator",
        correlation_id="RUN-1",
        payload={
            "run_id": "RUN-1",
            "workflow_run_id": "RUN-1",
            "objective": "deliver the product",
        },
    ))
    context = Namespace(state_dir=state_dir, config=config)
    monkeypatch.setattr("zf.cli.goal._context", lambda _args: context)

    result = _run_set(Namespace(
        objective="",
        status="active",
        reason="resume after repair",
        state_dir=None,
        timeout_seconds=None,
        token_budget=None,
        cost_budget_usd=None,
    ))

    assert result == 0
    updated = log.read_all()[-1]
    assert updated.type == "run.goal.updated"
    assert updated.payload["run_id"] == "RUN-1"
    assert updated.payload["workflow_run_id"] == "RUN-1"
    assert updated.correlation_id == "RUN-1"


def test_goal_cli_can_amend_active_run_limits(tmp_path, monkeypatch) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    config = ZfConfig(project=ProjectConfig(name="goal-cli"))
    log = EventLog(state_dir / "events.jsonl")
    EventWriter(log).append(ZfEvent(
        type="run.goal.started",
        actor="orchestrator",
        correlation_id="RUN-1",
        payload={"run_id": "RUN-1", "workflow_run_id": "RUN-1"},
    ))
    context = Namespace(state_dir=state_dir, config=config)
    monkeypatch.setattr("zf.cli.goal._context", lambda _args: context)

    result = _run_set(Namespace(
        objective="",
        status="active",
        reason="owner raised the run ceiling",
        state_dir=None,
        timeout_seconds=0.0,
        token_budget=0,
        cost_budget_usd=2000.0,
    ))

    assert result == 0
    updated = log.read_all()[-1]
    assert updated.payload["run_limits_patch"] == {
        "timeout_seconds": 0.0,
        "token_budget": 0,
        "cost_budget_usd": 2000.0,
    }


@pytest.mark.parametrize("terminal,expected", [
    ("run.goal.blocked", "in_progress"),
    ("run.goal.completed", "blocked"),
    ("run.cancelled", "blocked"),
    ("run.failed", "blocked"),
])
def test_goal_cli_reopens_workflow_parent_after_blocked_run_recovery(
    tmp_path, monkeypatch, terminal, expected,
):
    from zf.core.task.schema import Task
    from zf.core.task.store import TaskStore
    from zf.runtime.workflow_anchor import mark_workflow_managed_task

    state = tmp_path / '.zf'
    state.mkdir()
    config = ZfConfig(project=ProjectConfig(name='goal-cli'))
    store = TaskStore(state / 'kanban.json')
    store.add(mark_workflow_managed_task(Task(
        id='TASK-ROOT', title='Resume delivery', status='blocked',
        blocked_reason='workflow_budget_exceeded:tokens',
    )))
    writer = EventWriter(EventLog(state / 'events.jsonl'))
    writer.append(ZfEvent(
        type='workflow.invoke.accepted', actor='zf-cli', task_id='TASK-ROOT',
        correlation_id='RUN-1', payload={'workflow_run_id': 'RUN-1'},
    ))
    for kind in ('run.goal.started', terminal):
        writer.append(ZfEvent(
            type=kind, actor='zf-cli', task_id='TASK-ROOT', correlation_id='RUN-1',
            payload={'run_id': 'RUN-1', 'workflow_run_id': 'RUN-1'},
        ))
    monkeypatch.setattr('zf.cli.goal._context', lambda _args: Namespace(state_dir=state, config=config))
    args = Namespace(objective='', status='active', reason='owner recovery', state_dir=None)
    assert _run_set(args) == 0
    task = store.get('TASK-ROOT')
    assert task.status == expected
    if expected == 'in_progress':
        assert task.blocked_reason == ''
    assert _run_set(args) == 0
    changes = [e for e in writer.event_log.read_all() if e.type == 'task.status_changed']
    assert len(changes) == int(expected == 'in_progress')
    if changes:
        assert changes[0].payload['source'] == 'workflow_run_reopened'
