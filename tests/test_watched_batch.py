"""Batch wake behavior, durable boundaries, and failure safety."""
from types import SimpleNamespace

import pytest

from zf.cli.watched_batch import process_watched_batch
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.runtime.watcher import EventWatcher


def _process(events, *, orchestrator, offset=1234, **kwargs):
    options = dict(
        consumed_offset=offset,
        wake_patterns={'worker.heartbeat', 'prd.plan.child.completed'},
        wake_worthy_fn=lambda event: True,
        rate_limiter=SimpleNamespace(allow=lambda kind: True),
        orchestrator=orchestrator, simulation=False, event_log=None,
        stop_watcher=lambda: None,
    )
    options.update(kwargs)
    return process_watched_batch(events, **options)


def test_one_snapshot_runs_once_in_occurrence_order():
    events = [ZfEvent(type=kind) for kind in (
        'worker.heartbeat', 'provider.telemetry.observed',
        'prd.plan.child.completed', 'worker.heartbeat',
    )]
    calls = []
    orchestrator = SimpleNamespace(run_once=lambda **kwargs: calls.append(kwargs))
    assert _process(events, orchestrator=orchestrator)
    assert calls == [{'events': events, 'consumed_offset': 1234}]


@pytest.mark.parametrize('kind,worthy,allowed', [
    ('provider.telemetry.observed', True, True),
    ('worker.heartbeat', False, True),
    ('worker.heartbeat', True, False),
])
def test_non_waking_snapshot_waits_for_durable_tick(kind, worthy, allowed):
    def unexpected(**kwargs):
        pytest.fail('must not run or acknowledge observations prematurely')
    assert not _process(
        [ZfEvent(type=kind)], orchestrator=SimpleNamespace(run_once=unexpected),
        wake_worthy_fn=lambda event: worthy,
        rate_limiter=SimpleNamespace(allow=lambda kind: allowed),
    )


def test_failed_settlement_never_emits_simulation_done_or_stops(tmp_path):
    log = EventLog(tmp_path / 'events.jsonl')
    terminal = ZfEvent(type='run.goal.completed', payload={'run_id': 'RUN-1'})
    log.append(terminal)
    def fail(**kwargs):
        raise RuntimeError('settlement failed')
    with pytest.raises(RuntimeError, match='settlement failed'):
        _process([terminal], orchestrator=SimpleNamespace(run_once=fail),
                 simulation=True, event_log=log,
                 stop_watcher=lambda: pytest.fail('must settle before stop'))
    assert [e.type for e in log.read_all()] == ['run.goal.completed']


def test_reaction_append_is_left_for_next_snapshot(tmp_path):
    log = EventLog(tmp_path / 'events.jsonl')
    calls = []
    def run_once(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            log.append(ZfEvent(type='prd.plan.child.completed'))
    orchestrator = SimpleNamespace(run_once=run_once)
    watcher = EventWatcher(
        log.path, on_event=lambda line: None, event_log=log,
        on_batch_consumed=lambda events, offset: _process(
            events, orchestrator=orchestrator, offset=offset),
    )
    log.append(ZfEvent(type='worker.heartbeat'))
    boundary = log.current_offset()
    watcher.poll_once()
    assert len(calls) == 1
    assert calls[0]['consumed_offset'] == boundary < log.current_offset()
    watcher.poll_once()
    assert len(calls) == 2
    assert [e.type for e in calls[1]['events']] == ['prd.plan.child.completed']
    assert calls[1]['consumed_offset'] == log.current_offset()
