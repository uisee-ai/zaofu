"""Operator read scope survives the actual plan-review recovery path."""
import argparse
from types import SimpleNamespace

import pytest

from tests.test_control_actions_plan import _exec, _service
from zf.cli.plan_approval import _run
from zf.core.events.model import ZfEvent
from zf.core.task.schema import Task, TaskContract
from zf.core.task.store import TaskStore
from zf.runtime.plan_synth_handoff import build_plan_handoff_input_refs
from zf.runtime.replan_resynth import build_replan_resynth_event
from zf.runtime.sidecar_refs import hydrate_sidecar_ref


@pytest.mark.parametrize('surface', ['controlled', 'cli'])
def test_review_scope_reaches_resynth_and_full_snapshot(tmp_path, monkeypatch, surface):
    svc, log = _service(tmp_path)
    svc.config.workflow.admission_replan.enabled = True
    svc.config.workflow.admission_replan.resynth_trigger = 'prd.scan.completed'
    origin = svc.writer.append(ZfEvent(type='prd.scan.completed', correlation_id='RUN', payload={
        'workflow_run_id': 'RUN', 'trace_id': 'RUN', 'pdd_id': 'P1',
        'input_task_ids': ['STALE'],
    }))
    pending = svc.writer.append(ZfEvent(type='plan.approval.requested', correlation_id='RUN', payload={
        'plan_id': 'plan', 'workflow_run_id': 'RUN', 'trace_id': 'RUN', 'pdd_id': 'P1',
    }))
    scope = [' T1 ', 'T1']
    if surface == 'controlled':
        result = _exec(svc, log, 'plan-reject', {
            'plan_id': 'plan', 'reason': 'Bind complete task input bodies.',
            'request_event_id': pending.id, 'input_task_ids': scope,
        })
        assert result['ok'], result
    else:
        monkeypatch.chdir(tmp_path)
        assert _run(argparse.Namespace(plan_cmd='reject', state_dir=str(svc.state_dir),
            plan_id='plan', reason='Bind complete task input bodies.', input_task=scope)) == 0
    rejected = next(e for e in log.read_all() if e.type == 'plan.rejected')
    assert rejected.payload['input_task_ids'] == ['T1']
    plan = SimpleNamespace(pdd_id='P1', trace_id='RUN', source_event_id=rejected.id,
        source_event_type='plan.rejected', attempt=1, flow_kind='prd', input_task_ids=['STALE'])
    trigger = build_replan_resynth_event(plan=plan, events=log.read_all(), config=svc.config)
    assert trigger.payload['input_task_ids'] == ['T1']
    assert trigger.payload.get('task_ids') == origin.payload.get('task_ids')
    store = TaskStore(svc.state_dir / 'kanban.json')
    for tid in ['T1', 'T2']:
        store.add(Task(id=tid, title=tid, contract=TaskContract(feature_id='P1',
            acceptance_criteria=[f'Keep the full {tid} contract.'])))
    refs = build_plan_handoff_input_refs(state_dir=svc.state_dir, project_root=tmp_path,
        payload={'trigger_payload': trigger.payload}, source_event_id=trigger.id)
    context = next(r for r in refs if r['source_id'] == 'plan-rework-context')
    body = hydrate_sidecar_ref(svc.state_dir, context).payload
    snapshot = body['canonical_task_snapshot']
    assert [t['task_id'] for t in snapshot['scoped_tasks']] == ['T1']
    full = hydrate_sidecar_ref(svc.state_dir, snapshot['full_snapshot_ref']).payload
    assert full['tasks'][1]['contract']['acceptance_criteria'] == ['Keep the full T2 contract.']


def test_unscoped_rejection_does_not_reuse_old_operator_read_scope(tmp_path):
    svc, log = _service(tmp_path)
    svc.config.workflow.admission_replan.enabled = True
    svc.config.workflow.admission_replan.resynth_trigger = 'prd.scan.completed'
    svc.writer.append(ZfEvent(type='prd.scan.completed', correlation_id='RUN', payload={
        'trace_id': 'RUN', 'pdd_id': 'P1', 'input_task_ids': ['OLD'],
    }))
    rejection = svc.writer.append(ZfEvent(type='plan.rejected', correlation_id='RUN', payload={
        'plan_id': 'plan', 'reason': 'Review all affected work.',
    }))
    plan = SimpleNamespace(pdd_id='P1', trace_id='RUN', source_event_id=rejection.id,
        source_event_type='plan.rejected', attempt=1, flow_kind='prd', input_task_ids=['OLD'])
    trigger = build_replan_resynth_event(plan=plan, events=log.read_all(), config=svc.config)
    assert 'input_task_ids' not in trigger.payload


@pytest.mark.parametrize('scope', [None, 'T1', [1], [' ']])
def test_invalid_controlled_scope_does_not_consume_pending_approval(tmp_path, scope):
    svc, log = _service(tmp_path)
    svc.writer.append(ZfEvent(type='plan.approval.requested', payload={'plan_id': 'plan'}))
    result = _exec(svc, log, 'plan-reject', {
        'plan_id': 'plan', 'reason': 'Repair inputs.', 'input_task_ids': scope,
    })
    assert not result['ok']
    assert not any(e.type == 'plan.rejected' for e in log.read_all())
