import copy

import pytest

from tests.test_cancelled_run_workers import _runtime
from zf.core.events import EventWriter, ZfEvent
from zf.runtime.cancelled_run_workers import stop_cancelled_dispatch
from zf.runtime.orchestrator import Orchestrator
from zf.runtime.provider_turn_liveness import active_codex_turn_from_events


def stopped_events(tmp_path):
    runtime, stopped = _runtime(tmp_path)
    runtime.event_writer = EventWriter(runtime.event_log, default_origin='kernel')
    writer = runtime.event_writer
    writer.append(ZfEvent(type='workflow.operation.requested', origin='kernel',
        payload={'operation_id':'op1', 'request_hash':'hash1', 'workflow_run_id':'R1',
            'role_instance':'scan', 'task_id':'TASK-1', 'dispatch_id':'old',
            'active_attempt_id':'attempt1'}))
    writer.append(ZfEvent(type='codex.hook.user_prompt_submit', actor='scan',
        payload={'session_id':'s1', 'turn_id':'t1'}))
    cancel = writer.append(ZfEvent(type='fanout.cancelled', origin='kernel',
        payload={'fanout_id':'old-fanout'}))
    assert Orchestrator._active_codex_turn(runtime, 'scan')
    assert stop_cancelled_dispatch(runtime, role_instance='scan', task_id='TASK-1',
        dispatch_id='old', events=[], cancellation=cancel)
    assert stopped == ['scan']
    return runtime, writer.event_log.read_all()


def test_actual_cancellation_releases_dispatch_turn_fence(tmp_path):
    runtime, events = stopped_events(tmp_path)
    assert Orchestrator._active_codex_turn(runtime, 'scan') is None
    assert not any(e.type in {'codex.hook.stop','task.done','test.passed'} for e in events)


@pytest.mark.parametrize('fault', ['untrusted_stop', 'untrusted_start', 'untrusted_cancel',
    'unconfirmed', 'missing_start', 'dispatch_id', 'operation_id', 'active_attempt_id',
    'request_hash', 'fanout_cancelled_event_id', 'malformed_source'])
def test_inexact_stop_retains_activity(tmp_path, fault):
    _, events = stopped_events(tmp_path)
    events = copy.deepcopy(events)
    start = next(e for e in events if e.type=='worker.state.changed' and e.payload.get('to')=='cancelling')
    end = next(e for e in events if e.payload.get('provider_stop_confirmed'))
    if fault=='untrusted_stop': end.origin='external'
    elif fault=='untrusted_start': start.origin='external'
    elif fault=='untrusted_cancel': next(e for e in events if e.type=='fanout.cancelled').origin='external'
    elif fault=='unconfirmed': end.payload['provider_stop_confirmed']=False
    elif fault=='missing_start': events.remove(start)
    elif fault=='malformed_source': end.payload['source']=[]
    else: end.payload[fault]='different'
    assert active_codex_turn_from_events(events,'scan')['turn_id']=='t1'


def test_new_turn_during_stop_is_not_retired(tmp_path):
    _, events = stopped_events(tmp_path)
    end = next(e for e in events if e.payload.get('provider_stop_confirmed'))
    events.insert(events.index(end), ZfEvent(type='codex.hook.user_prompt_submit', actor='scan',
        payload={'session_id':'s2','turn_id':'t2'}))
    assert active_codex_turn_from_events(events,'scan')['turn_id']=='t2'
