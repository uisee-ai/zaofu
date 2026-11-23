"""Cost blackout measures open dispatches, not completed Reader history."""
import pytest

from tests.test_cost_blackout_watchdog import _env, _ts
from zf.core.events.model import ZfEvent
from zf.runtime.tick_services import TickServiceIntervals, TickServiceState, _emit_cost_blackout_if_needed


def _dispatch(writer, *, child='product', dispatch='attempt-2', age=300):
    body = {'workflow_run_id':'workflow-1', 'fanout_id':'scan-1', 'child_id':child, 'run_id':dispatch}
    writer.append(ZfEvent(type='fanout.child.dispatched', ts=_ts(age), origin='kernel', payload=body))
    return body


def _fires(state, log, writer):
    return _emit_cost_blackout_if_needed(event_log=log,event_writer=writer,state_dir=state,
        state=TickServiceState(),intervals=TickServiceIntervals())


@pytest.mark.parametrize('terminal', ['fanout.child.completed', 'fanout.child.failed'])
def test_closed_reader_does_not_trigger_blackout(tmp_path, terminal):
    state, log, writer = _env(tmp_path)
    body = _dispatch(writer)
    writer.append(ZfEvent(type=terminal,origin='kernel',payload=body))
    assert not _fires(state,log,writer)


def test_completed_newest_reader_does_not_hide_open_sibling(tmp_path):
    state,log,writer=_env(tmp_path)
    _dispatch(writer,child='tech',dispatch='attempt-tech',age=400)
    body=_dispatch(writer)
    writer.append(ZfEvent(type='fanout.child.completed',origin='kernel',payload=body))
    assert _fires(state,log,writer)
    assert log.read_all()[-1].payload['last_dispatch_ts'] == log.read_all()[0].ts


@pytest.mark.parametrize('fault', ['old_dispatch', 'other_run', 'other_child', 'untrusted'])
def test_unbound_terminal_cannot_hide_active_dispatch(tmp_path, fault):
    state,log,writer=_env(tmp_path)
    body=_dispatch(writer)
    origin='kernel'
    if fault == 'old_dispatch':body['run_id']='attempt-1'
    elif fault == 'other_run':body['workflow_run_id']='workflow-2'
    elif fault == 'other_child':body['child_id']='tech'
    else:origin='worker'
    writer.append(ZfEvent(type='fanout.child.completed',origin=origin,payload=body))
    assert _fires(state,log,writer)
