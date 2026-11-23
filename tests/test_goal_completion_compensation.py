"""Thin Judge consumes admitted historical evidence after controlled compensation."""
from dataclasses import asdict
from copy import deepcopy
import json

import pytest

from tests.test_plan_completion_reuse import recovery
from tests.test_terminal_completion_restore import incident, planned, restore
from tests.test_goal_closure_retained_authority import issues
from zf.runtime.candidate_child_authority import candidate_child_authority_issues
from zf.runtime.task_contract_authority import TaskContractAuthorityService


def compensate(r):
    plan = planned(r)
    assert restore(r, manifest_ref=plan['manifest_ref'],
        reviewed_digest=plan['manifest']['manifest_digest'], reason='Exact reviewed compensation')['ok']
    return plan


def test_goal_admission_consumes_original_proof_after_compensation_without_rewriting(incident):
    r = incident
    result, snapshot = r.pre_compensation_goal
    state = r.runtime.state_dir
    original = (state / result['contract_snapshot_ref']).read_bytes()
    assert issues(r, result)  # The accidental completion cannot pass.
    compensate(r)
    # Model the actual bridge: a new closure copies the prior admitted Verify children.
    pin = next(e for e in r.runtime.event_log.read_all() if e.type == 'flow.goal.closed')
    r.emit('flow.goal.closed', deepcopy(pin.payload))
    before = r.runtime.event_log.read_all()
    assert not issues(r, result)
    assert (state / result['contract_snapshot_ref']).read_bytes() == original
    assert [e.id for e in r.runtime.event_log.read_all()] == [e.id for e in before]
    assert 'completion_restore_ref' not in snapshot['child_authorities'][0]
    # Only the pinned Goal path can derive this view; a new Candidate contract
    # still must carry its explicit exact restore certificate.
    assert candidate_child_authority_issues(state, before, r.restored_candidate,
        snapshot['child_authorities'])


def test_actual_goal_builder_and_thin_judge_preserve_admitted_pre_compensation_proof(incident):
    from zf.core.config.schema import ZfConfig
    from zf.runtime.goal_closure_bridge import GoalClosureBridgeMixin

    r = incident
    old, snapshot = r.pre_compensation_goal
    compensate(r)
    bridge = GoalClosureBridgeMixin()
    bridge.state_dir, bridge.project_root = r.runtime.state_dir, r.runtime.project_root
    bridge.config = ZfConfig()
    identity = {**snapshot, 'candidate_head_commit': old['target_commit'],
        'closure_fact_ref': old['closure_fact_ref'], 'closure_fact_digest': old['closure_fact_digest']}
    fields = bridge._goal_closure_read_contract(r.runtime.event_log.read_all(),
        workflow_run_id=old['workflow_run_id'], goal_id='FLOW-ANCHOR', identity=identity,
        claim_set={}, payload=r.source.payload)
    current = json.loads((bridge.state_dir / fields['contract_snapshot_ref']).read_text())
    assert current['child_authorities'] == snapshot['child_authorities']
    assert fields['input_result_refs']
    result = {**old, **fields}
    r.emit('flow.goal.closed', result)
    assert not issues(r, result)


@pytest.mark.parametrize('ref', [None, {}, {'ref': 'wrong', 'sha256': '0' * 64}])
def test_exact_goal_pin_does_not_replace_existing_bad_compensation_ref(incident, monkeypatch, ref):
    r = incident
    compensate(r)
    result, snapshot = deepcopy(r.pre_compensation_goal)
    snapshot['child_authorities'][0]['completion_restore_ref'] = ref
    # Isolate the strict currentness consumer after an already established pin.
    monkeypatch.setattr('zf.runtime.candidate_child_authority._goal_pins_candidate_verify', lambda *args: True)
    from zf.runtime.candidate_child_authority import goal_child_authority_issues
    assert goal_child_authority_issues(r.runtime.state_dir, r.runtime.event_log.read_all(),
        snapshot=snapshot, result=result)


@pytest.mark.parametrize('fault', ['manifest', 'owner', 'mutation', 'candidate',
    'replacement', 'original_verify', 'original_identity', 'original_reuse', 'unadmitted_snapshot'])
def test_goal_compensation_never_widens_original_authority(incident, monkeypatch, fault):
    r = incident
    plan = compensate(r)
    result, snapshot = deepcopy(r.pre_compensation_goal)
    state = r.runtime.state_dir
    if fault == 'manifest':
        (state / plan['manifest_ref']['ref']).write_text('{}')
    elif fault in {'owner', 'mutation'}:
        events = r.runtime.event_log.read_all()
        if fault == 'owner':
            events = [e for e in events if not (e.type == 'operator.intent.created'
                and e.payload.get('action_request', {}).get('reviewed_digest') == plan['manifest']['manifest_digest'])]
        else:
            events = [e for e in events if e.id != r.mutation.id]
        monkeypatch.setattr(r.runtime.event_log, 'read_all', lambda: events)
    elif fault == 'candidate':
        r.emit('candidate.ready', dict(r.restored_candidate.payload))
    elif fault == 'replacement':
        task = r.runtime.task_store.get('TASK-1')
        contract = asdict(task.contract)
        contract['behavior'] = 'A later requirement'
        TaskContractAuthorityService(task_store=r.runtime.task_store,
            event_writer=r.runtime.event_writer, state_dir=state).replace(task,
                contract=contract, source='new-requirement', reopen_terminal=True,
                task_updates={'status': 'backlog'})
        r.runtime.task_store.update(task.id, status='done')
    elif fault == 'original_reuse':
        ref = snapshot['child_authorities'][0]['completion_reuse_ref']
        (state / ref['ref']).write_text('{}')
    else:
        child = snapshot['child_authorities'][0]
        child['verification_event_id' if fault == 'original_verify' else 'contract_revision'] = 'foreign'
        ref = r.sidecar(snapshot)
        result.update(contract_snapshot_ref=ref['ref'], contract_snapshot_digest=ref['sha256'])
        if fault != 'unadmitted_snapshot':
            pin = next(e for e in r.runtime.event_log.read_all() if e.type == 'flow.goal.closed')
            r.emit('flow.goal.closed', {**pin.payload, **result})
    assert issues(r, result)
