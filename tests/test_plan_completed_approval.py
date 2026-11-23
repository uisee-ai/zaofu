"""Late approval reuses exact native and retained evidence, never task IDs alone."""
from dataclasses import asdict
from types import SimpleNamespace

import pytest

from tests.test_plan_completion_reuse import recovery, action, reviewed
from zf.core.events.model import ZfEvent
from zf.runtime.plan_completed_approval import suppress_completed_plan_approval
from zf.runtime.task_contract_authority import TaskContractAuthorityService


@pytest.mark.parametrize('fault', ['none', 'package', 'generation', 'head', 'task', 'authority', 'active', 'map-body', 'port-body'])
def test_late_approval_requires_exact_current_completion_proof(recovery, monkeypatch, fault):
    r = recovery
    assert action(r, reviewed_digest=reviewed(r), approval_ref='owner:reviewed',
                  reuse_rationale='Same acceptance; explicit completion reuse')['ok']
    r.emit('candidate.ready', {**r.candidate.payload, **r.source.payload})
    r.runtime.event_writer.append(ZfEvent(type='plan.approved', actor='operator', origin='kernel',
        payload={'plan_id': r.source.id}))
    loaded = SimpleNamespace(**{k: r.source.payload[k] for k in
        ['workflow_run_id', 'task_map_generation', 'plan_artifact_package_ref', 'plan_artifact_package_digest']},
        task_items=[{'task_id': 'TASK-1'}], pdd_id='FLOW-ANCHOR',
        task_map_ref=r.source.payload['task_map_ref'])
    monkeypatch.setattr('zf.runtime.plan_completed_approval._git_commit',
                        lambda *_: ('d' if fault == 'head' else 'c') * 40)
    if fault == 'package':
        loaded.plan_artifact_package_digest = '0' * 64
    elif fault == 'map-body':
        altered = r.sidecar({'tasks': [{'task_id': 'TASK-1', 'description': 'Additional acceptance'}],
                            'target_commit': 'c' * 40})
        loaded.task_map_ref = altered['ref']
        r.source.payload['task_map_ref'] = altered['ref']
    elif fault == 'port-body':
        r.source.payload['plan_ports'] = {'issue_spec': {'schema_version': 'issue-spec.v1',
                                                       'summary': 'New requirements'}}
    elif fault == 'generation':
        loaded.task_map_generation = 'different-generation'
    elif fault == 'task':
        loaded.task_items.append({'task_id': 'UNVERIFIED'})
    elif fault == 'authority':
        # Reopen through the canonical store, then revise through authority CAS.
        old = r.runtime.task_store.get('TASK-1')
        old.status = 'backlog'
        r.runtime.task_store.reopen(old)
        contract = asdict(old.contract)
        contract['behavior'] = 'Changed requirement'
        TaskContractAuthorityService(task_store=r.runtime.task_store, event_writer=r.runtime.event_writer,
            state_dir=r.runtime.state_dir).replace(old, contract=contract, source='test_revision')
        r.runtime.task_store.update('TASK-1', status='done')
    elif fault == 'active':
        r.emit('workflow.operation.requested', {'operation_id': 'new-work', 'task_id': 'TASK-1',
            'request_hash': 'new-work-hash'}, 'TASK-1')
    before = asdict(r.runtime.task_store.get('TASK-1'))

    result = suppress_completed_plan_approval(r.runtime, trigger_event=r.source, loaded=loaded, stage_id='impl')

    assert result is (fault == 'none')
    assert asdict(r.runtime.task_store.get('TASK-1')) == before
    if fault == 'none':
        assert suppress_completed_plan_approval(r.runtime, trigger_event=r.source, loaded=loaded, stage_id='impl')
        suppressed = [e for e in r.runtime.event_log.read_all() if e.type == 'fanout.retrigger.suppressed']
        assert len(suppressed) == 1
        assert suppressed[0].payload['reason'] == 'approved_plan_tasks_already_integrated'
