"""Real-provider oracle counterexamples; no paid provider in this unit tier."""
import json
from pathlib import Path

import pytest

from zf.core.events import EventLog, EventWriter
from zf.runtime.call_result_envelope import write_immutable_json_sidecar
from tests.e2e.prd_ui import recovery


@pytest.mark.parametrize('fault', ['', 'wrong_run', 'wrong_task', 'unconsumed', 'premature_plan', 'direct_adoption'])
@pytest.mark.parametrize('decision_kind', ['approved', 'rejected'])
def test_owner_oracle_requires_same_run_consumed_answer_before_new_plan(fault, decision_kind):
    from types import SimpleNamespace
    from tests.e2e.prd_ui.audit import check_owner_checkpoint
    def event(kind, id, **payload):
        return SimpleNamespace(type=kind, id=id, actor='web', task_id='T1', correlation_id='R1',
                               payload={'workflow_run_id': 'R1', **payload})
    owner = event('approval.requested', 'owner', owner_route='plan_synth_owner_decision', checkpoint_id='C1', plan_id='candidate')
    decision = event(f'replan.owner_decision.{decision_kind}', 'decision', checkpoint_id='C1', surface='web', direct_adoption=False)
    resolved = event('plan.synth.owner_decision.resolved', 'resolved', decision_event_id='decision')
    plan = event('plan.approval.requested', 'plan', plan_id='final')
    invoke = event('workflow.invoke.requested', 'invoke')
    decision.task_id = plan.task_id = None
    events = [invoke, owner, decision, resolved, plan]
    if fault == 'wrong_run':
        resolved.payload['workflow_run_id'] = 'R2'
    if fault == 'wrong_task': plan.task_id = 'T2'
    if fault == 'unconsumed': events.remove(resolved)
    if fault == 'premature_plan': events = [invoke, owner, decision, plan, resolved]
    if fault == 'direct_adoption': decision.payload['direct_adoption'] = True
    if fault:
        with pytest.raises(AssertionError): check_owner_checkpoint(events, 'R1')
    else:
        assert check_owner_checkpoint(events, 'R1')['final_plan_id'] == 'final'


@pytest.mark.parametrize('fault', ['', 'duplicate_retry', 'completed_redelivered', 'wrong_generation', 'wrong_receipt', 'no_native_recovery'])
def test_recovery_oracle_rejects_duplicate_or_unproven_delivery(tmp_path, monkeypatch, fault):
    (tmp_path/'zf.yaml').write_text('version: "1.0"\nproject: {name: proof, state_dir: .state}\n')
    state = tmp_path/'.state'
    writer = EventWriter(EventLog(state/'events.jsonl'))
    for kind in ('abort', 'completed'):
        body = {'channel_id': f'ch-codex-{kind}', 'request_id': kind, 'run_generation': 1, 'provider_session_id': kind}
        writer.emit('channel.agent.reply.requested', payload=body)
        failed = writer.emit('channel.agent.reply.failed', payload={**body, 'failure_status': 'delivery_unknown'})
        if kind == 'abort':
            receipt = write_immutable_json_sidecar(state, {'failure_event_id': failed.id if fault != 'wrong_receipt' else 'wrong',
                'provider_session_id': kind, 'native_turn_id': 'old'}, root='artifacts/recovery', kind='receipt',
                schema_version='test.v1', created_by='unit')
            body = {**body, 'run_generation': 2, 'native_abort_receipt_ref': receipt}
            writer.emit('channel.agent.reply.requested', payload=body)
            if fault == 'duplicate_retry': writer.emit('channel.agent.reply.requested', payload=body)
            writer.emit('channel.agent.reply.completed', payload={**body, 'run_generation': 1 if fault == 'wrong_generation' else 2})
        elif fault == 'completed_redelivered':
            writer.emit('channel.agent.reply.requested', payload={**body, 'run_generation': 2})
    writer.event_log.close()
    def native(sid):
        rows = [{'type': 'model_context', 'model': 'gpt-6.1-sol', 'effort': 'high'}]
        if sid == 'abort': rows.append({'type': 'turn_aborted', 'turn_id': 'old'})
        if fault != 'no_native_recovery' or sid != 'abort': rows.append({'type': 'task_complete', 'turn_id': 'new'})
        return rows
    monkeypatch.setattr(recovery, 'native_metadata', native)
    if fault:
        with pytest.raises(AssertionError): recovery.audit(tmp_path)
    else:
        assert recovery.audit(tmp_path)['abort']['request_id'] == 'abort'


def test_native_metadata_excludes_provider_reasoning_and_prompt(tmp_path, monkeypatch):
    monkeypatch.setenv('CODEX_HOME', str(tmp_path))
    path=tmp_path/'sessions/2026/11/10/rollout-test-session.jsonl'; path.parent.mkdir(parents=True)
    records = [
        {'type': 'response_item', 'payload': {'type': 'reasoning', 'text': 'private reasoning'}},
        {'type': 'event_msg', 'payload': {'type': 'agent_message', 'message': 'private answer'}},
        {'type': 'event_msg', 'timestamp': 'now', 'payload': {'type': 'task_complete', 'turn_id': 'turn'}},
        {'type': 'turn_context', 'payload': {'model': 'gpt-6.1-sol', 'effort': 'high', 'prompt': 'private prompt'}},
    ]
    path.write_text(''.join(json.dumps(row)+'\n' for row in records))
    result = recovery.native_metadata('session')
    assert result == [{'type': 'task_complete', 'turn_id': 'turn', 'timestamp': 'now'},
                      {'type': 'model_context', 'model': 'gpt-6.1-sol', 'effort': 'high'}]


@pytest.mark.parametrize('args', [
    ['--provider', 'fake', '--journey', 'channel-recovery'],
    ['--provider', 'fake', '--journey', 'kanban', '--owner-checkpoint'],
])
def test_real_recovery_tier_cannot_be_claimed_by_fake(args):
    import subprocess, sys
    result = subprocess.run([sys.executable, '-m', 'tests.e2e.prd_ui.run', *args],
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 2
    assert 'requires' in result.stderr
