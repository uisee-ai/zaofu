"""First Reader delivery after pinning failed before call preparation."""
from dataclasses import replace

import pytest

from zf.core.events import ZfEvent
from zf.runtime.fanout import FanoutChild
from zf.runtime.reader_rebuild_admission import reader_rebuild_error
from tests.test_candidate_reader_retry_authority import _prepared_candidate


def _unprepared(tmp_path):
    runtime, role, context, payload = _prepared_candidate(tmp_path, prepare=False)
    context = replace(context, expected_children=[FanoutChild(child_id='verify',
        role_instance=role.instance_id, target_ref=context.target_ref, payload=payload)])
    started = runtime.event_writer.append(context.started_event())
    runtime.event_writer.emit('run.started', payload={'workflow_run_id': context.trace_id})
    attempt = f'run-{context.fanout_id}-verify'
    p = dict(fanout_id=context.fanout_id, stage_id=context.stage_id, child_id='verify',
             role_instance=role.instance_id, workflow_run_id=context.trace_id,
             trace_id=context.trace_id, run_id=attempt, target_ref=context.target_ref)
    wait = runtime.event_writer.append(ZfEvent(type='fanout.child.dispatch_deferred',
        origin='kernel', payload={**p, 'reason': 'reader_environment_busy'}))
    failed = runtime.event_writer.append(ZfEvent(type='fanout.child.failed', origin='kernel',
        causation_id=started.id, payload={**p, 'reason': 'reader environment wait exhausted',
        'first_wait_event_id': wait.id, 'failure_class': 'reader_environment_wait_exhausted'}))
    action = dict(fanout_id=context.fanout_id, fanout_child_id='verify',
                  workflow_run_id=context.trace_id, active_attempt_id=attempt, source_event_id=failed.id)
    return runtime, context, failed, action


def test_controlled_unprepared_wait_first_delivery(tmp_path):
    from zf.runtime.control_actions import ControlledActionService
    runtime, context, failed, action = _unprepared(tmp_path)
    assert reader_rebuild_error(runtime.state_dir, runtime.event_log.read_all(), action) == ''
    svc = ControlledActionService(runtime.state_dir, runtime.event_writer, config=runtime.config,
                                 project_root=runtime.project_root, actor='operator', source='operator', surface='cli')
    requested = runtime.event_writer.append(ZfEvent(type='operator.intent.created', origin='external'))
    result = svc.execute(action='child-rebuild', requested_action='child-rebuild', payload=action, requested=requested)
    assert result['ok'], result
    repair = next(e for e in runtime.event_log.read_all() if e.type == 'repair.action.requested')
    runtime.run_once(events=[repair])
    runtime.run_once(events=[repair])
    dispatched = [e for e in runtime.event_log.read_all() if e.type == 'fanout.child.dispatched']
    assert len(dispatched) == 1
    assert dispatched[0].causation_id == failed.id
    assert dispatched[0].payload['operation_id']
    assert dispatched[0].payload['retry_of_run_id'] == action['active_attempt_id']
    assert not dispatched[0].payload['payload'].get('retry_of_operation_id')
    assert len(runtime.transport.sent) == 1


@pytest.mark.parametrize('fault', ['paused', 'cancelled', 'dispatched', 'foreign_attempt', 'foreign_child', 'foreign_run', 'other_failure'])
def test_unprepared_wait_does_not_relax_current_binding(tmp_path, fault):
    runtime, context, failed, action = _unprepared(tmp_path)
    if fault in {'paused', 'cancelled'}:
        runtime.event_writer.emit('run.' + fault, payload={'workflow_run_id': context.trace_id})
    elif fault == 'dispatched':
        runtime.event_writer.append(ZfEvent(type='fanout.child.dispatched', origin='kernel', payload=failed.payload))
    elif fault == 'other_failure':
        newer = runtime.event_writer.append(ZfEvent(type='fanout.child.failed', origin='kernel',
            payload={**failed.payload, 'failure_class': 'unknown'}))
        action['source_event_id'] = newer.id
    else:
        action[{'foreign_attempt':'active_attempt_id', 'foreign_child':'fanout_child_id', 'foreign_run':'workflow_run_id'}[fault]] = 'foreign'
    assert reader_rebuild_error(runtime.state_dir, runtime.event_log.read_all(), action)
    assert not runtime.transport.sent


def test_native_resident_routes_unprepared_wait_and_waits_for_exact_effect(tmp_path):
    from tests.test_run_manager_reader_rebuild import consume
    from zf.runtime.run_manager import _settle_pending_action_effects
    runtime, context, failed, action = _unprepared(tmp_path)
    runtime.event_writer.append(ZfEvent(type='run.manager.agent.recommendation', actor='run-manager',
        payload={'recommended_route': 'controlled_action', 'action': 'child-rebuild',
                 'summary': 'released stale holder; retry the failed first delivery', 'action_payload': action}))
    consume(runtime)
    intent = next(e for e in runtime.event_log.read_all() if e.type == 'repair.action.requested')
    runtime.run_once(events=[intent])
    events = runtime.event_log.read_all()
    assert _settle_pending_action_effects(events, runtime.event_writer) == 0
    dispatched = next(e for e in events if e.type == 'fanout.child.dispatched')
    runtime.event_writer.append(ZfEvent(type='fanout.child.completed', origin='kernel',
        payload={**dispatched.payload, 'run_id':'wrong-attempt'}))
    assert _settle_pending_action_effects(runtime.event_log.read_all(), runtime.event_writer) == 0
    runtime.event_writer.append(ZfEvent(type='fanout.child.completed', origin='kernel', payload=dispatched.payload))
    assert _settle_pending_action_effects(runtime.event_log.read_all(), runtime.event_writer) == 1


def test_omitting_existing_operation_cannot_bypass_attempt_identity(tmp_path):
    from tests.test_reader_undelivered_retry import _waiting
    runtime, context, operation_id, failed = _waiting(tmp_path)
    p = failed.payload
    action = dict(fanout_id=context.fanout_id, fanout_child_id='verify', workflow_run_id=context.trace_id,
                  active_attempt_id=p['run_id'], source_event_id=failed.id)
    assert reader_rebuild_error(runtime.state_dir, runtime.event_log.read_all(), action)


def test_execution_rechecks_unprepared_obligation_after_approval(tmp_path):
    from zf.runtime.control_actions import ControlledActionService
    runtime, context, failed, action = _unprepared(tmp_path)
    svc = ControlledActionService(runtime.state_dir, runtime.event_writer, config=runtime.config,
                                 project_root=runtime.project_root, actor='operator', source='operator', surface='cli')
    requested = runtime.event_writer.append(ZfEvent(type='operator.intent.created', origin='external'))
    assert svc.execute(action='child-rebuild', requested_action='child-rebuild', payload=action, requested=requested)['ok']
    repair = next(e for e in runtime.event_log.read_all() if e.type == 'repair.action.requested')
    runtime.event_writer.emit('run.paused', payload={'workflow_run_id':context.trace_id})
    runtime.run_once(events=[repair])
    assert not runtime.transport.sent
    assert any(e.type == 'repair.action.rejected' for e in runtime.event_log.read_all())
