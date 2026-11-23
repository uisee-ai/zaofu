"""Controlled terminal compensation binds intent, archive, completion and current CAS."""
from dataclasses import asdict
from copy import deepcopy
import json

import pytest

from tests.test_plan_completion_reuse import recovery, action, reviewed
from zf.core.events.model import ZfEvent
from zf.runtime.candidate_retained_tasks import candidate_child_proofs
from zf.runtime.task_contract_authority import TaskContractAuthorityService
from zf.runtime.terminal_completion_restore import SOURCE


@pytest.fixture
def incident(recovery, monkeypatch):
    r = recovery
    assert action(r, reviewed_digest=reviewed(r), approval_ref='owner:reviewed',
                  reuse_rationale='Same acceptance; explicit completion reuse')['ok']
    candidate = r.emit('candidate.ready', {**r.candidate.payload, **r.source.payload})
    from tests.test_goal_closure_retained_authority import closure
    r.pre_compensation_goal = closure(r)
    original = asdict(r.runtime.task_store.get('TASK-1'))
    r.runtime.event_writer.append(ZfEvent(type='plan.approved', actor='operator', origin='kernel',
        payload={'plan_id': r.source.id}))
    contract = dict(original['contract'])
    contract['behavior'] = 'Rejected replay replacement'
    TaskContractAuthorityService(task_store=r.runtime.task_store, event_writer=r.runtime.event_writer,
        state_dir=r.runtime.state_dir).replace(r.runtime.task_store.get('TASK-1'), contract=contract,
        source='refactor_replan_adoption', correlation_id=r.source.payload['workflow_run_id'],
        reopen_terminal=True, task_updates={'status': 'backlog', 'completed_at': None},
        audit_payload={'old_status': 'done', 'reopened_from_terminal': True,
                       'new_task_map_ref': r.source.payload['task_map_ref']})
    mutation = next(e for e in reversed(r.runtime.event_log.read_all())
                    if e.type == 'task.contract.revision.applied')
    r.runtime.task_store.update('TASK-1', status='done', completed_at='2026-02-01T00:00:00+00:00')
    r.emit('fanout.cancelled', {'trigger_event_id': r.source.id, 'reason': 'terminal_tasks'})
    r.emit('run.paused', {})
    monkeypatch.setattr('zf.runtime.terminal_completion_restore._git_commit', lambda *_: 'c' * 40)
    r.original, r.mutation, r.restored_candidate = original, mutation, candidate
    return r


def restore(r, **extra):
    payload = {'source_event_id': r.source.id, 'restore_completed_tasks': True, **extra}
    intent = r.runtime.event_writer.append(ZfEvent(type='operator.intent.created', origin='external',
        actor='operator', payload={'action': 'stage-retrigger', 'action_request': payload}))
    return r.service.execute(action='stage-retrigger', requested_action='stage-retrigger',
                             requested=intent, payload=payload)


def planned(r):
    result = restore(r, dry_run=True, mutation_event_ids=[r.mutation.id])
    assert result['ok'], result
    return result


def test_restore_preserves_completion_with_forward_authority_and_no_business_redrive(incident):
    r = incident
    plan = planned(r)
    before = r.runtime.event_log.read_all()
    result = restore(r, manifest_ref=plan['manifest_ref'], reviewed_digest=plan['manifest']['manifest_digest'],
                     reason='Delegated Owner reviewed exact compensation manifest')
    assert result['ok'], result
    task = r.runtime.task_store.get('TASK-1')
    assert task.status == 'done' and task.completed_at == r.original['completed_at']
    assert asdict(task.contract) == r.original['contract']
    assert task.contract_authority_revision != r.original['contract_authority_revision']
    assert task.contract_authority_sequence == plan['manifest']['tasks'][0]['expected']['contract_authority_sequence'] + 1
    after = r.runtime.event_log.read_all()[len(before):]
    assert not any(e.type in {'task.done', 'task_map.ready', 'workflow.operation.requested'} for e in after)
    proofs = candidate_child_proofs(r.runtime, r.runtime.event_log.read_all(), r.restored_candidate)
    assert proofs['TASK-1']['completion_restore_ref'] == plan['manifest_ref']
    archive = r.runtime.task_store._archive_file(r.runtime.task_store._today()).read_bytes()
    assert restore(r, manifest_ref=plan['manifest_ref'], reviewed_digest=plan['manifest']['manifest_digest'],
                   reason='Replay same reviewed compensation')['ok']
    assert r.runtime.task_store._archive_file(r.runtime.task_store._today()).read_bytes() == archive
    assert len([e for e in r.runtime.event_log.read_all() if e.type == 'task.status_changed'
                and e.payload.get('source') == SOURCE]) == 1
    from zf.runtime.task_contract_authority_doctor import build_task_contract_authority_report
    report = build_task_contract_authority_report(r.runtime.state_dir, r.runtime.event_log.read_all())
    assert not [issue for issue in report['issues'] if issue['code'] == 'store_authority_revision_mismatch']


@pytest.mark.parametrize('fault', ['digest', 'resumed', 'mixed-options', 'mutated-proof',
                                   'replacement', 'moved-candidate', 'worker-pause'])
def test_restore_rejects_changed_scope_or_evidence_without_task_writes(incident, monkeypatch, fault):
    r = incident
    plan = planned(r)
    before = asdict(r.runtime.task_store.get('TASK-1'))
    args = {'manifest_ref': plan['manifest_ref'], 'reviewed_digest': plan['manifest']['manifest_digest'],
            'reason': 'Exact reviewed compensation'}
    if fault == 'digest':
        args['reviewed_digest'] = '0' * 64
    elif fault == 'resumed':
        r.emit('run.resumed', {})
    elif fault == 'mixed-options':
        args['reuse_completed_plan'] = True
    elif fault == 'replacement':
        from zf.runtime.candidate_continuation import digest
        manifest = deepcopy(plan['manifest'])
        manifest['tasks'][0]['replacement']['contract']['behavior'] = 'Unreviewed replacement'
        manifest['manifest_digest'] = digest({k: v for k, v in manifest.items() if k != 'manifest_digest'})
        args['manifest_ref'] = r.sidecar(manifest)
        args['reviewed_digest'] = manifest['manifest_digest']
    elif fault == 'moved-candidate':
        monkeypatch.setattr('zf.runtime.terminal_completion_restore._git_commit', lambda *_: 'd' * 40)
    elif fault == 'worker-pause':
        r.runtime.event_writer.append(ZfEvent(type='run.paused', origin='worker', actor='worker',
            payload={'workflow_run_id': r.source.payload['workflow_run_id']}))
    else:
        path = r.runtime.state_dir / r.mutation.payload['mutation_ref']['ref']
        path.write_text('{}')
    assert not restore(r, **args)['ok']
    assert asdict(r.runtime.task_store.get('TASK-1')) == before


def test_restore_replays_after_atomic_store_write_before_events(incident, monkeypatch):
    r = incident
    plan = planned(r)
    original = r.runtime.event_writer.append
    def crash(event):
        if event.type == 'task.contract.revision.applied' and event.payload.get('source') == SOURCE:
            raise RuntimeError('crash after archive append')
        return original(event)
    monkeypatch.setattr(r.runtime.event_writer, 'append', crash)
    args = {'manifest_ref': plan['manifest_ref'], 'reviewed_digest': plan['manifest']['manifest_digest'],
            'reason': 'Exact reviewed compensation'}
    with pytest.raises(RuntimeError, match='crash after'):
        restore(r, **args)
    monkeypatch.setattr(r.runtime.event_writer, 'append', original)
    assert restore(r, **args)['ok']
    assert candidate_child_proofs(r.runtime, r.runtime.event_log.read_all(), r.restored_candidate)


def test_completion_consumers_require_restore_ref_and_reject_later_authority(incident):
    from zf.runtime.candidate_verification_authority import prepare_candidate_verification_authority
    from zf.runtime.candidate_child_authority import candidate_child_authority_issues

    r = incident
    plan = planned(r)
    assert restore(r, manifest_ref=plan['manifest_ref'], reviewed_digest=plan['manifest']['manifest_digest'],
                   reason='Exact reviewed compensation')['ok']
    authority = prepare_candidate_verification_authority(r.runtime, payload={},
        workflow_run_id=r.source.payload['workflow_run_id'], task_id='FLOW-ANCHOR', source_event_id=r.source.id)
    snapshot = json.loads((r.runtime.state_dir / authority['contract_snapshot_ref']).read_text())
    children = snapshot['child_authorities']
    events = r.runtime.event_log.read_all()
    assert children[0]['completion_restore_ref'] == plan['manifest_ref']
    assert not candidate_child_authority_issues(r.runtime.state_dir, events, r.restored_candidate, children)
    lost = [{key: value for key, value in child.items() if key != 'completion_restore_ref'} for child in children]
    assert candidate_child_authority_issues(r.runtime.state_dir, events, r.restored_candidate, lost)
    task = r.runtime.task_store.get('TASK-1')
    contract = asdict(task.contract)
    contract['behavior'] = 'Later genuine requirement change'
    TaskContractAuthorityService(task_store=r.runtime.task_store, event_writer=r.runtime.event_writer,
        state_dir=r.runtime.state_dir).replace(task, contract=contract, source='later_requirement',
            reopen_terminal=True, task_updates={'status': 'backlog'})
    r.runtime.task_store.update('TASK-1', status='done')
    with pytest.raises(ValueError, match='current authority changed'):
        candidate_child_proofs(r.runtime, r.runtime.event_log.read_all(), r.restored_candidate)


def test_worker_cannot_authorize_terminal_compensation(incident):
    r = incident
    plan = planned(r)
    before = asdict(r.runtime.task_store.get('TASK-1'))
    payload = {'source_event_id': r.source.id, 'restore_completed_tasks': True,
        'manifest_ref': plan['manifest_ref'], 'reviewed_digest': plan['manifest']['manifest_digest'],
        'reason': 'Worker claims approval'}
    intent = r.runtime.event_writer.append(ZfEvent(type='operator.intent.created', actor='worker',
        origin='worker', payload={'action': 'stage-retrigger', 'action_request': payload}))
    result = r.service.execute(action='stage-retrigger', requested_action='stage-retrigger',
                               requested=intent, payload=payload)
    assert not result['ok']
    assert asdict(r.runtime.task_store.get('TASK-1')) == before


def test_restored_replay_rejects_missing_original_mutation_event(incident):
    from zf.runtime.candidate_continuation import digest

    r = incident
    plan = planned(r)
    assert restore(r, manifest_ref=plan['manifest_ref'], reviewed_digest=plan['manifest']['manifest_digest'],
                   reason='Exact reviewed compensation')['ok']
    manifest = deepcopy(plan['manifest'])
    manifest['tasks'][0]['mutation_event_id'] = 'absent-mutation'
    manifest['manifest_digest'] = digest({k: v for k, v in manifest.items() if k != 'manifest_digest'})
    ref = r.sidecar(manifest)
    archive = r.runtime.task_store._archive_file(r.runtime.task_store._today()).read_bytes()
    previous = [e.id for e in r.runtime.event_log.read_all() if e.payload.get('source') == SOURCE]
    result = restore(r, manifest_ref=ref, reviewed_digest=manifest['manifest_digest'],
                     reason='Replay with a changed incident reference')
    assert not result['ok']
    assert r.runtime.task_store._archive_file(r.runtime.task_store._today()).read_bytes() == archive
    assert [e.id for e in r.runtime.event_log.read_all() if e.payload.get('source') == SOURCE] == previous
