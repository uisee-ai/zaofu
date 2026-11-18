"""Controlled recovery keeps accepted readers and replaces cancelled calls."""
from pathlib import Path
from contextlib import nullcontext
from unittest.mock import patch

import pytest

from zf.core.events.model import ZfEvent
from zf.runtime.call_result_runtime import workflow_operation_service
from zf.runtime.workflow_operation import load_workflow_operation
from tests.test_reader_fanout_runtime import (
    _durable_reader_state, _manifest, _start_fanout, _state,
)
from tests.test_repair_action_executor import _append_request
from zf.runtime.call_result_envelope import write_immutable_json_sidecar
from zf.runtime.artifact_read_ledger import read_attempt_artifact
from zf.runtime.sidecar_refs import hydrate_sidecar_ref
from zf.runtime.reader_fanout_retry import prepare_fresh_reader_retry


def test_reader_retry_preserves_required_writer_evidence(tmp_path: Path) -> None:
    state, log, _transport, orch, config, child = _durable_reader_state(tmp_path)
    descriptor = write_immutable_json_sidecar(
        state, {'command_receipts': [{'exit_code': 0}]},
        root='impl-self-check', kind='impl_self_check', schema_version='impl-self-check.v1',
        created_by='test',
    )
    child['payload'].update({
        'impl_self_check_ref': descriptor['ref'],
        'impl_self_check_digest': descriptor['sha256'],
    })
    child['payload']['required_reads'].append({
        'source_id': 'impl-self-check', 'artifact_id': Path(descriptor['ref']).name,
        'artifact_sha256': descriptor['sha256'], 'json_path': '$',
    })
    previous = next(e for e in log.read_all() if e.type == 'fanout.child.dispatched')
    # A fresh reader may replace a cancelled call only after the role is idle.
    # The controlled-action test below covers this lifecycle through run_once.
    old = child['payload']
    workflow_operation_service(orch).cancel(
        operation_id=old['operation_id'], request_hash=old['request_hash'],
        workflow_run_id='run-durable', task_id='T-VERIFY', reason='controlled_reader_retry',
    )
    orch._set_worker_state(config.roles[0].instance_id, 'idle',
                           task_id='T-VERIFY', reason='original_reader_cancelled')
    prepare_fresh_reader_retry(
        orch, manifest=_manifest(state, child['fanout_id']), child=child,
        role=config.roles[0], attempt=1, previous_dispatch=previous,
    )
    payload = child['payload']
    manifest = hydrate_sidecar_ref(state, payload['attempt_source_manifest']).payload
    # Exercise the same controlled source lookup used by `artifact read --required`.
    for required in payload['required_reads']:
        read_attempt_artifact(
            state, manifest=manifest, source_id=required['source_id'],
            artifact_id=required['artifact_id'], json_path=required['json_path'],
        )
    assert payload['impl_self_check_ref'] == descriptor['ref']
    assert payload['impl_self_check_digest'] == descriptor['sha256']


def test_controlled_reader_retry_replaces_cancelled_operation(tmp_path: Path) -> None:
    state, log, transport, orch, _config, child = _durable_reader_state(tmp_path)
    old = child['payload']
    workflow_operation_service(orch).cancel(
        operation_id=old['operation_id'], request_hash=old['request_hash'],
        workflow_run_id='run-durable', task_id='T-VERIFY',
        reason='workflow_budget_exceeded:wall_clock',
    )
    request = _append_request(
        state, action_id='reader-budget-retry', kind='rerun_fanout_child',
        idempotency_key='reader-budget-retry',
        fanout_id=child['fanout_id'], fanout_child_id=child['child_id'],
    )
    orch.run_once([request])
    events = log.read_all()
    assert any(e.type == 'repair.action.applied' for e in events)
    retry = [e for e in events if e.type == 'fanout.child.dispatched'][-1]
    new = retry.payload['payload']
    assert new['operation_id'] != old['operation_id']
    assert new['parent_operation_id'] == old['operation_id']
    assert new['retry_of_operation_id'] == old['operation_id']
    assert new['result_scratch_ref'] != old['result_scratch_ref']
    assert new['attempt_id'] != old['attempt_id']
    assert load_workflow_operation(log, old['operation_id'])['status'] == 'cancelled'
    assert load_workflow_operation(log, new['operation_id'])['status'] == 'running'
    briefing = transport.sent[-1][1].read_text()
    assert new['operation_id'] in briefing
    assert old['result_scratch_ref'] not in briefing
    sent = len(transport.sent)
    orch.run_once([request])
    assert len(transport.sent) == sent
    orch.run_once([ZfEvent(
        type='verify.child.completed', actor=child['role_instance'],
        task_id='T-VERIFY', correlation_id='run-durable', payload={
            **old, 'fanout_id': child['fanout_id'], 'child_id': child['child_id'],
            'run_id': child['run_id'], 'status': 'completed',
        },
    )])
    current = _manifest(state, child['fanout_id'])['children'][0]
    assert current['status'] == 'dispatched'
    assert current['payload']['operation_id'] == new['operation_id']
    assert not any(e.type == 'review.approved' for e in log.read_all())


@pytest.mark.parametrize('terminal_path', ['aggregate', 'lost_turn'])
def test_controlled_retry_after_failed_aggregate_remains_recoverable(tmp_path: Path, terminal_path):
    from zf.runtime.fanout_recovery_runtime import has_nonterminal_reader_fanouts
    from zf.runtime.fanout import FanoutManifestProjector

    state, log, _, orch, _, child = _durable_reader_state(tmp_path)
    old = child['payload']
    workflow_operation_service(orch).cancel(
        operation_id=old['operation_id'], request_hash=old['request_hash'],
        workflow_run_id='run-durable', task_id='T-VERIFY', reason='reader_failed',
    )
    orch.event_writer.append(ZfEvent(type='fanout.child.failed', payload={
        'fanout_id': child['fanout_id'], 'child_id': child['child_id'],
        'run_id': child['run_id'], 'reason': 'provider closed without result',
    }))
    failed = orch.event_writer.append(ZfEvent(type='fanout.aggregate.completed', payload={
        'fanout_id': child['fanout_id'], 'status': 'failed',
    }))
    request = _append_request(
        state, action_id='retry-failed-aggregate', kind='rerun_fanout_child',
        idempotency_key='retry-failed-aggregate',
        fanout_id=child['fanout_id'], fanout_child_id=child['child_id'],
    )
    orch.run_once([request])
    manifest = orch._fanout_manifest(child['fanout_id'])
    assert manifest['aggregate']['status'] == 'pending'
    assert manifest['aggregate']['recovered_from_event_id'] == failed.id
    assert has_nonterminal_reader_fanouts(orch)
    assert manifest['children'][0]['reason'] == ''
    # Restart/replay must preserve the live retry, not restore the old failure.
    rebuilt = FanoutManifestProjector(state).rebuild(child['fanout_id'], log.read_all())
    assert rebuilt['aggregate']['status'] == 'pending'
    new = manifest['children'][0]
    if terminal_path == 'lost_turn':
        orch.event_writer.append(ZfEvent(type='provider.turn.closed', actor=child['role_instance'],
            payload={'backend': 'codex', 'instance_id': child['role_instance'],
                     'turn_id': 'new-reader-turn'}))
        assert orch._recover_lost_reader_fanout_dispatches(log.read_all())
        losses = [e for e in log.read_all() if e.type == 'fanout.child.dispatch_lost']
        assert losses[-1].payload['run_id'] == new['run_id']
        return
    # A crash after the terminal child receipt but before aggregation recovers.
    orch.event_writer.append(ZfEvent(type='fanout.child.failed', payload={
        'fanout_id': child['fanout_id'], 'child_id': child['child_id'],
        'run_id': new['run_id'], 'reason': 'new attempt failed',
    }))
    orch._recover_incomplete_reader_fanout_aggregates(log.read_all())
    aggregates = [e for e in log.read_all() if e.type == 'fanout.aggregate.completed']
    assert len(aggregates) == 2
    assert aggregates[-1].id != failed.id


@pytest.mark.parametrize('resume,reason,ready', [
    (True, 'workflow_run_terminal:run.goal.blocked', True),
    (False, 'workflow_run_terminal:run.goal.blocked', False),
    (True, 'operator_cancelled', False),
])
@pytest.mark.parametrize('idle_recovery', [False, True])
@pytest.mark.parametrize('failed', [False, True])
def test_reader_aggregate_continues_only_after_authorized_run_recovery(
    tmp_path: Path, resume: bool, reason: str, ready: bool,
    idle_recovery: bool, failed: bool,
) -> None:
    state, log, transport, orch = _state(tmp_path)
    _start_fanout(orch)
    started = next(e for e in log.read_all() if e.type == 'fanout.started')
    fanout_id = started.payload['fanout_id']
    children = _manifest(state, fanout_id)['children']

    def finish(child, *, failure=False):
        orch.run_once([ZfEvent(
            type='workflow.child.failed' if failure else 'workflow.child.completed',
            actor=child['role_instance'],
            correlation_id='trace-1', payload={
                'fanout_id': fanout_id, 'trace_id': 'trace-1',
                'stage_id': 'review-candidate', 'child_id': child['child_id'],
                'run_id': child['run_id'], 'role_instance': child['role_instance'],
                'status': 'failed' if failure else 'completed',
            },
        )])

    finish(children[0])
    for event_type, payload in [
        ('run.goal.blocked', {'workflow_run_id': 'trace-1', 'reason': 'budget'}),
        ('fanout.cancelled', {'fanout_id': fanout_id, 'reason': reason}),
        *([('run.goal.updated', {'workflow_run_id': 'trace-1', 'status': 'active'})]
          if resume else []),
    ]:
        orch.event_writer.append(ZfEvent(
            type=event_type, actor='operator', correlation_id='trace-1', payload=payload,
        ))
    with (patch.object(orch, '_evaluate_reader_fanout') if idle_recovery else nullcontext()):
        finish(children[1], failure=failed)
    orch.run_once([])
    assert sum(e.type == 'review.approved' for e in log.read_all()) == int(ready and not failed)
    assert sum(e.type == 'review.rejected' for e in log.read_all()) == int(ready and failed)
    assert sum(
        e.type == 'fanout.child.dispatched' and e.payload.get('fanout_id') == fanout_id
        for e in log.read_all()
    ) == 2
