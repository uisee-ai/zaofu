"""Policy replacement is admitted only between Runs, before an invoke exists."""
import json
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from tests.test_workflow_config_apply import _proposal, _apply_payload
from zf.core.config.loader import load_config
from zf.core.events import EventLog, EventWriter, ZfEvent
from zf.runtime import workflow_config_handoff as handoff
from zf.runtime.workflow_config_apply import WorkflowConfigApplyService, WorkflowConfigApplyError
from zf.runtime.workflow_config_consumer import consumer_policy_digest


def _case(tmp_path, monkeypatch):
    proposal, ref, validation = _proposal(tmp_path)
    state = tmp_path / '.zf'
    config_path = tmp_path / 'candidate.yaml'
    config = load_config(config_path)
    owner = {'owner_pid': 777, 'epoch': 'old'}
    ready = {'owner_pid': 777, 'epoch': 'old', 'execution_policy_digest': 'old',
             'tmux_session': config.session.tmux_session}
    monkeypatch.setattr(handoff, '_runtime_owner', lambda _: dict(owner))
    monkeypatch.setattr(handoff, '_read', lambda _: dict(ready))
    def stop(*args, **kwargs):
        owner.clear()
        return SimpleNamespace(returncode=0)
    def start(**kwargs):
        owner.update(owner_pid=888, epoch='new')
        ready.update(owner, execution_policy_digest=consumer_policy_digest(config))
        return {'ok': True, 'pid': 888, 'status': 'running', 'launched': True}
    monkeypatch.setattr(handoff, 'subprocess', SimpleNamespace(run=Mock(side_effect=stop), TimeoutExpired=TimeoutError))
    monkeypatch.setattr(handoff, 'ensure_project_runtime', Mock(side_effect=start))
    writer = EventWriter(EventLog(state / 'events.jsonl'))
    args = dict(config_path=config_path, state_dir=state, writer=writer,
                payload={'workflow_proposal_ref': ref, 'effective_config_ref': proposal['effective_config_ref']})
    return args, owner, ready, proposal, ref, validation


def test_idle_old_consumer_restarts_and_confirms_exact_policy(tmp_path, monkeypatch):
    args, _, _, *_ = _case(tmp_path, monkeypatch)
    result = handoff.prepare_workflow_consumer(**args)
    assert result['runtime_start']['config_handoff']
    assert result['runtime_start']['previous_owner_pid'] == 777
    stop = handoff.subprocess.run.call_args
    assert stop.args[0][-3:] == ['stop', '--fast', '--include-run-manager']
    assert stop.kwargs['cwd'] == tmp_path
    assert stop.kwargs['env']['ZF_STATE_DIR'] == str(args['state_dir'])
    assert handoff.prepare_workflow_consumer(**args) == {}
    assert handoff.subprocess.run.call_count == 1
    assert not any(e.type == 'workflow.invoke.requested' for e in args['writer'].event_log.read_all())


@pytest.mark.parametrize('event', [
    ZfEvent(type='run.started', correlation_id='old', payload={'workflow_run_id': 'old'}),
    ZfEvent(type='workflow.invoke.requested', payload={'workflow_run_id': 'queued'}),
    ZfEvent(type='channel.agent.reply.requested', payload={'channel_id': 'ch', 'request_id': 'reply'}),
    ZfEvent(type='channel.agent.reply.failed', payload={'channel_id': 'ch', 'request_id': 'reply', 'failure_status': 'delivery_unknown'}),
])
def test_live_work_preserves_owner_and_blocks_new_policy(tmp_path, monkeypatch, event):
    args, owner, _, *_ = _case(tmp_path, monkeypatch)
    args['writer'].append(event)
    result = handoff.prepare_workflow_consumer(**args)
    assert result['status'] == 'STOP'
    assert result['workflow_invoke_status'] == 'not_requested'
    assert owner['owner_pid'] == 777
    handoff.subprocess.run.assert_not_called()


@pytest.mark.parametrize('change', ['wrong_session', 'starting', 'missing_receipt', 'unconfirmed_new_policy', 'corrupt_snapshot'])
def test_handoff_fails_closed(tmp_path, monkeypatch, change):
    args, _, ready, *_ = _case(tmp_path, monkeypatch)
    if change == 'wrong_session': ready['tmux_session'] = 'another-project'
    if change == 'starting': ready['epoch'] = 'other'
    if change == 'missing_receipt': ready.clear()
    if change == 'unconfirmed_new_policy': handoff.ensure_project_runtime.side_effect = lambda **_: {'ok': True}
    if change == 'corrupt_snapshot': args['payload']['effective_config_ref']['sha256'] = 'f' * 64
    assert handoff.prepare_workflow_consumer(**args)['status'] == 'STOP'


def test_config_apply_cannot_change_active_run_policy_even_if_owner_is_down(tmp_path, monkeypatch):
    import tests.test_workflow_config_apply as fixtures
    original_config = fixtures._config
    def config_with_gate(path, *, lanes):
        original_config(path, lanes=lanes)
        with path.open('a') as stream:
            stream.write('  workflow: {plan_approval: ' + ('true' if lanes == 2 else 'false') + '}\n')
    monkeypatch.setattr(fixtures, '_config', config_with_gate)
    proposal, ref, validation = _proposal(tmp_path)
    state = tmp_path / '.zf'
    original = (tmp_path / 'zf.yaml').read_bytes()
    EventWriter(EventLog(state / 'events.jsonl')).append(ZfEvent(
        type='run.started', correlation_id='running', payload={'workflow_run_id': 'running'}))
    with pytest.raises(WorkflowConfigApplyError, match='现有工作'):
        WorkflowConfigApplyService(state_dir=state, project_root=tmp_path).apply(_apply_payload(proposal, ref, validation))
    assert (tmp_path / 'zf.yaml').read_bytes() == original


def test_submit_wrapper_serializes_concurrent_ui_and_cli(tmp_path):
    _proposal(tmp_path)
    import time
    order = []
    @handoff.serialized_workflow_submit
    def submit(**kwargs):
        order.append('start')
        time.sleep(.02)
        order.append('end')
    with ThreadPoolExecutor(max_workers=3) as executor:
        list(executor.map(lambda _: submit(config_path=tmp_path / 'zf.yaml'), range(3)))
    assert order == ['start', 'end'] * 3


def test_cancelled_unconsumed_invoke_does_not_block_future_policy(tmp_path):
    events = [ZfEvent(type="workflow.invoke.requested", id="invoke", payload={"workflow_run_id": "old"}),
              ZfEvent(type="run.cancelled", payload={"workflow_run_id": "old"})]
    assert handoff.handoff_blockers(tmp_path, events) == []


def test_blocked_run_keeps_policy_even_before_task_creation(tmp_path):
    events = [ZfEvent(type="run.goal.blocked", payload={"workflow_run_id": "old"})]
    assert handoff.handoff_blockers(tmp_path, events) == ["Run old (blocked)"]


def test_submit_lock_respects_explicit_project_root(tmp_path, monkeypatch):
    _proposal(tmp_path)
    elsewhere = tmp_path / "different-cwd"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    @handoff.serialized_workflow_submit
    def submit(**kwargs):
        return {"ok": True}
    from pathlib import Path
    assert submit(config_path=Path("zf.yaml"), intake_path=Path("intake.json"), project_root=tmp_path)["ok"]
