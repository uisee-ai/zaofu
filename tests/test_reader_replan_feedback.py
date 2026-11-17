"""Authorized recovery retains new operator feedback without rewriting history."""
import pytest

from zf.core.events.model import ZfEvent
from zf.runtime.plan_synth_handoff import build_plan_handoff_input_refs
from zf.runtime.sidecar_refs import hydrate_sidecar_ref
from tests.test_control_actions_recovery import _req
from tests.test_reader_repair_target import setup_reader


def _apply(service, payload):
    return service.execute(action='stage-replan-new-generation',
        requested_action='stage-replan-new-generation', payload=payload, requested=_req(payload))


def test_operator_feedback_is_bound_to_new_generation_and_replay(tmp_path):
    svc, origin, payload, _ = setup_reader(tmp_path)
    payload.pop('repair_target')
    payload['operator_feedback'] = ['Wire the cold setup command.', 'Bind task-local input ports.']
    result = _apply(svc, payload)
    assert result['ok'], result
    trigger = next(e for e in svc.writer.event_log.read_all() if e.id == result['stage_trigger_event_id'])
    assert trigger.payload['operator_feedback'] == payload['operator_feedback']
    feedback = trigger.payload['rework_feedback']
    assert feedback[0]['message'] == 'flow.discovery.failed'
    assert [f['message'] for f in feedback[1:]] == payload['operator_feedback']
    assert trigger.payload['contract_snapshot_ref'] == origin.payload['contract_snapshot_ref']
    sources = build_plan_handoff_input_refs(state_dir=svc.state_dir, project_root=tmp_path,
        payload={'trigger_payload': trigger.payload}, source_event_id=trigger.id)
    context = next(s for s in sources if s['source_id'] == 'plan-rework-context')
    body = hydrate_sidecar_ref(svc.state_dir, context).payload
    assert body['rework_feedback'] == feedback
    assert body['operator_feedback'] == payload['operator_feedback']
    assert _apply(svc, payload)['replayed']
    changed = _apply(svc, {**payload, 'operator_feedback': ['Different instructions']})
    assert not changed['ok']
    assert 'different operator feedback' in changed['reason']
    assert len([e for e in svc.writer.event_log.read_all() if e.type == 'run.goal.updated']) == 1


def test_operator_input_scope_reaches_index_without_changing_dispatch(tmp_path):
    from zf.core.task.schema import Task, TaskContract
    from zf.core.task.store import TaskStore

    svc, origin, payload, _ = setup_reader(tmp_path)
    payload.pop('repair_target')
    store = TaskStore(svc.state_dir / 'kanban.json')
    for task_id in ('TASK-A', 'TASK-B'):
        store.add(Task(id=task_id, title=task_id, contract=TaskContract(
            feature_id='T1', acceptance_criteria=[f'AC-{task_id}: complete contract'],
        )))
    payload['input_task_ids'] = [' TASK-A ', 'UNKNOWN', 'TASK-A']
    result = _apply(svc, payload)
    assert result['ok'], result
    trigger = next(e for e in svc.writer.event_log.read_all() if e.id == result['stage_trigger_event_id'])
    assert trigger.payload['input_task_ids'] == ['TASK-A', 'UNKNOWN']
    assert trigger.payload.get('task_ids') == origin.payload.get('task_ids')
    assert trigger.payload['contract_snapshot_ref'] == origin.payload['contract_snapshot_ref']
    refs = build_plan_handoff_input_refs(state_dir=svc.state_dir, project_root=tmp_path,
        payload={'pdd_id': 'T1', 'trigger_payload': trigger.payload}, source_event_id=trigger.id)
    context = next(s for s in refs if s['source_id'] == 'plan-rework-context')
    body = hydrate_sidecar_ref(svc.state_dir, context).payload
    snapshot = body['canonical_task_snapshot']
    assert body['input_task_ids'] == ['TASK-A', 'UNKNOWN']
    assert [t['task_id'] for t in snapshot['scoped_tasks']] == ['TASK-A']
    assert snapshot['unresolved_task_ids'] == ['UNKNOWN']
    full = hydrate_sidecar_ref(svc.state_dir, snapshot['full_snapshot_ref']).payload
    assert {t['task_id'] for t in full['tasks']} == {'TASK-A', 'TASK-B'}
    index = next(t for t in snapshot['tasks'] if t['task_id'] == 'TASK-B')
    position = int(index['full_task_json_path'].split('[')[1].split(']')[0])
    assert full['tasks'][position]['contract']['acceptance_criteria'] == ['AC-TASK-B: complete contract']
    assert _apply(svc, {**payload, 'input_task_ids': ['UNKNOWN', 'TASK-A']})['replayed']
    changed = _apply(svc, {**payload, 'input_task_ids': ['TASK-B']})
    assert not changed['ok']
    assert 'different input task scope' in changed['reason']
    assert len([e for e in svc.writer.event_log.read_all() if e.type == 'run.goal.updated']) == 1


@pytest.mark.parametrize('bad', ['T1', None, [1], ['  '], [{'task_id': 'T1'}]])
def test_invalid_input_scope_cannot_reopen_run(tmp_path, bad):
    svc, _, payload, _ = setup_reader(tmp_path)
    payload.pop('repair_target')
    result = _apply(svc, {**payload, 'input_task_ids': bad})
    assert not result['ok']
    assert not any(e.type == 'run.goal.updated' for e in svc.writer.event_log.read_all())


def test_fixed_input_retry_cannot_silently_ignore_new_read_scope(tmp_path):
    svc, _, payload, _ = setup_reader(tmp_path)
    result = _apply(svc, {**payload, 'repaired_failure_event_id': 'failure',
                         'input_task_ids': ['T1']})
    assert not result['ok']
    assert 'fixed-input repair cannot change input task scope' in result['reason']
    assert not any(e.type == 'run.goal.updated' for e in svc.writer.event_log.read_all())


def test_explicit_candidate_checkpoint_is_bound_and_replay_safe(tmp_path, monkeypatch):
    svc, _, payload, _ = setup_reader(tmp_path)
    payload.pop('repair_target')
    refs = [{'source_id': 'child-result-planner', 'ref': 'candidate', 'sha256': 'a' * 64}]
    monkeypatch.setattr('zf.runtime.plan_synth_recovery_refs.resume_failed_critic_candidate',
                        lambda state, events, source, synth_id: refs if synth_id == 'synth' else [])
    payload['resume_candidate_synth_event_id'] = 'synth'
    result = _apply(svc, payload)
    assert result['ok'], result
    trigger = next(e for e in svc.writer.event_log.read_all() if e.id == result['stage_trigger_event_id'])
    assert trigger.payload['previous_plan_candidate_refs'] == refs
    assert trigger.payload['resume_candidate_synth_event_id'] == 'synth'
    assert _apply(svc, payload)['replayed']
    assert not _apply(svc, {**payload, 'resume_candidate_synth_event_id': 'other'})['ok']


@pytest.mark.parametrize('bad', ['text', None, [1], ['  '], [{'message': 'text'}]])
def test_invalid_operator_feedback_cannot_reopen_run(tmp_path, bad):
    svc, _, payload, _ = setup_reader(tmp_path)
    payload.pop('repair_target')
    result = _apply(svc, {**payload, 'operator_feedback': bad})
    assert not result['ok']
    assert not any(e.type == 'run.goal.updated' for e in svc.writer.event_log.read_all())


def test_later_generation_without_feedback_does_not_reuse_old_input(tmp_path):
    svc, _, payload, _ = setup_reader(tmp_path)
    payload.pop('repair_target')
    first = _apply(svc, {**payload, 'operator_feedback': ['Previous repair direction.'],
                         'input_task_ids': ['T1']})
    assert first['ok']
    svc.writer.append(ZfEvent(type='fanout.started', correlation_id='RUN', payload={
        'fanout_id': 'second-reader', 'stage_id': 'prd-post-verify-discovery',
        'trigger_event_id': first['stage_trigger_event_id'],
    }))
    failure = svc.writer.append(ZfEvent(type='flow.discovery.failed', task_id='T1',
        correlation_id='RUN', payload={'workflow_run_id': 'RUN', 'fanout_id': 'second-reader',
        'reason': 'New failure', 'stage_replan_generation': first['stage_replan_generation']}))
    escalation = svc.writer.append(ZfEvent(type='human.escalate', task_id='T1',
        correlation_id='RUN', causation_id=failure.id, payload={
            'failure_class': 'stage_replan_cap_exhausted', 'source_event_id': failure.id,
            'allowed_actions': ['operator_review', 'start_new_generation'],
        }))
    second_payload = {**payload, 'escalation_event_id': escalation.id}
    second = _apply(svc, second_payload)
    assert second['ok'], second
    trigger = next(e for e in svc.writer.event_log.read_all() if e.id == second['stage_trigger_event_id'])
    assert 'operator_feedback' not in trigger.payload
    assert 'input_task_ids' not in trigger.payload
    assert _apply(svc, second_payload)['replayed']


def test_canonical_dispatch_pins_operator_repair_as_required_lossless_input(tmp_path):
    from zf.core.task.schema import Task, TaskContract
    from zf.core.task.store import TaskStore
    from zf.runtime.artifact_query.handoff import CanonicalHandoffResolver
    from zf.runtime.task_contract_snapshot import build_task_contract_snapshot, write_task_contract_snapshot, current_task_contract_identity
    from zf.runtime.artifact_read_ledger import canonical_required_reads, read_attempt_artifact
    state = tmp_path / 'custom-state'
    state.mkdir()
    task = Task(id='T1', title='Repair review', status='in_progress', contract=TaskContract(
        acceptance_criteria=['AC1'], verification='node --test', scope=['app/'], evidence_contract={'task_map_generation':'G1'}))
    TaskStore(state / 'kanban.json').add(task)
    ref = write_task_contract_snapshot(state, build_task_contract_snapshot(task,
        workflow_run_id='run', task_map_generation_id='G1', base_commit='base', task_ref='task/T1'))
    base = {**current_task_contract_identity(task), 'output_profile_id':'implementation',
        'base_commit':'base', 'task_ref':'task/T1', 'task_map_generation':'G1',
        'contract_snapshot_ref':ref['ref'], 'contract_snapshot_digest':ref['sha256']}
    message = 'Read exact candidate host receipt, not an approval. ' * 1000
    payload = {**base, 'trigger_payload':{'operator_feedback':[message],
        'rework_feedback':[{'message':message, 'source':'operator', 'source_event_id':'approval'}]}}
    resolver = CanonicalHandoffResolver(state_dir=state, project_root=tmp_path, config=None)
    manifest, _ = resolver.resolve_payload(payload=payload, workflow_run_id='run', task_id='T1',
        attempt_id='a1', dispatch_id='a1', source_event_id='retry')
    source = next(s for s in manifest['sources'] if s['source_id']=='critical-reader-repair-context')
    for profile in ['implementation','task-verify','candidate-verify','workflow-read']:
        assert any(r['source_id']==source['source_id'] and r['json_path']=='$'
                   for r in canonical_required_reads(manifest, output_profile_id=profile))
    content = read_attempt_artifact(state, manifest=manifest, source_id=source['source_id'],
        artifact_id=source['artifact_id'], json_path='$')['content']
    assert message in content
    assert 'approval' in content
    changed, _ = resolver.resolve_payload(payload={**base,'operator_feedback':['new evidence']},
        workflow_run_id='run',task_id='T1',attempt_id='a2',dispatch_id='a2',source_event_id='retry2')
    assert changed['source_snapshot']['identity_digest'] != manifest['source_snapshot']['identity_digest']
