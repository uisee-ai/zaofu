"""Does normal page continuation preserve the first idle-fault retry?"""
import time
import pytest
from tests import test_reader_fanout_runtime as fixtures
from tests.test_reader_progress_continuation import read_page, close_turn


@pytest.mark.parametrize('pages', [0, 1, 4])
def test_first_idle_fault_can_retry_after_read_progress(tmp_path, monkeypatch, pages):
    make_config = fixtures.ZfConfig
    def configured(**kwargs):
        config = make_config(**kwargs)
        config.workflow.stages[0].aggregate.max_retries = 1
        return config
    monkeypatch.setattr(fixtures, 'ZfConfig', configured)
    state, log, transport, runtime, config, child = fixtures._durable_reader_state(tmp_path)
    for page in range(pages):
        read_page(state, child, page)
        close_turn(runtime, page)
    assert len(transport.sent) == 1 + pages
    runtime._set_worker_state('verify-1', 'idle', force=True)
    runtime._now = lambda: time.time() + 4000
    runtime._check_fanout_timeouts()
    assert not [e for e in log.read_all() if e.type == 'fanout.child.failed']
    assert len(transport.sent) == 2 + pages
    runtime._set_worker_state('verify-1', 'idle', force=True)
    runtime._check_fanout_timeouts()
    failed = [e for e in log.read_all() if e.type == 'fanout.child.failed']
    assert len(failed) == 1 and failed[0].payload['reason'] == 'idle'
    assert len(transport.sent) == 2 + pages


@pytest.mark.parametrize('invalid', ['origin', 'action', 'operation', 'attempt', 'request',
                                    'role', 'cause', 'future', 'missing_claim', 'blank_binding'])
def test_only_bound_kernel_continuations_are_exempt(tmp_path, invalid):
    from dataclasses import replace
    from zf.runtime.reader_retry_budget import reader_fault_dispatch_count

    state, log, _, runtime, _, child = fixtures._durable_reader_state(tmp_path)
    read_page(state, child, 0)
    close_turn(runtime, 0)
    events = log.read_all()
    dispatches = [e for e in events if e.type == 'fanout.child.dispatched']
    claim = next(e for e in events if e.type == 'worker.continuation.claimed')
    assert reader_fault_dispatch_count(dispatches, events) == 1
    changed = claim
    if invalid == 'origin':
        changed = replace(claim, origin='worker')
    elif invalid == 'role':
        changed = replace(claim, actor='other')
    elif invalid in {'action', 'operation', 'attempt', 'request'}:
        key = {'action': 'recovery_action', 'operation': 'operation_id',
               'attempt': 'attempt_id', 'request': 'request_hash'}[invalid]
        changed = replace(claim, payload={**claim.payload, key: 'other'})
    elif invalid == 'blank_binding':
        changed = replace(claim, payload={**claim.payload, 'request_hash': None})
        last = dispatches[-1]
        dispatches[-1] = replace(last, payload={**last.payload, 'request_hash': None,
            'payload': {**last.payload.get('payload', {}), 'request_hash': None}})
    elif invalid == 'cause':
        dispatches[-1] = replace(dispatches[-1], causation_id='other')
    events = [changed if e.id == claim.id else e for e in events]
    if invalid in {'future', 'missing_claim'}:
        events = [e for e in events if e.id != claim.id]
        if invalid == 'future':
            events.append(claim)
    assert reader_fault_dispatch_count(dispatches, events) == 2
