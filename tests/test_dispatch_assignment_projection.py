"""Dispatch ownership stays current without rereading provider transcript bodies."""
from pathlib import Path

from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.runtime.dispatch_routing_queries import DispatchRoutingQueriesMixin


class Query(DispatchRoutingQueriesMixin):
    def __init__(self, path: Path):
        self.event_log = EventLog(path)


def test_dispatch_query_reads_only_appended_bytes_and_preserves_reassignment(tmp_path, monkeypatch):
    query = Query(tmp_path / 'events.jsonl')
    log = query.event_log
    log.append(ZfEvent(type='task.dispatched', task_id='T', payload={'assignee': 'dev'}))
    offsets = []
    read = log.read_from_offset

    def observed(offset):
        offsets.append(offset)
        return read(offset)

    monkeypatch.setattr(log, 'read_from_offset', observed)
    assert query._latest_dispatched_per_task() == {'T': 'dev'}
    first_end = log.current_offset()
    log.append(ZfEvent(type='agent.text', payload={'text': 'x' * 100_000}))
    assert query._latest_dispatched_per_task() == {'T': 'dev'}
    assert offsets == [0, first_end]
    log.append(ZfEvent(type='task.assigned', task_id='T', payload={'assignee': 'verify'}))
    assert query._latest_dispatched_per_task() == {}
    log.append(ZfEvent(type='fanout.child.dispatched', task_id='T', payload={'role_instance': 'verify'}))
    assert query._latest_dispatched_per_task() == {'T': 'verify'}
    log.append(ZfEvent(type='fanout.child.completed', task_id='T'))
    assert query._latest_dispatched_per_task() == {}


def test_dispatch_query_handles_partial_tail_and_larger_atomic_replacement(tmp_path):
    query = Query(tmp_path / 'events.jsonl')
    path = query.event_log.path
    query.event_log.append(ZfEvent(type='task.dispatched', task_id='old', payload={'assignee': 'dev'}))
    assert query._latest_dispatched_per_task() == {'old': 'dev'}
    with path.open('ab') as handle:
        handle.write(b'{"type": "task.requeued", "task_id": "old", "payload": {}')
    assert query._latest_dispatched_per_task() == {'old': 'dev'}
    with path.open('ab') as handle:
        handle.write(b'}\n')
    assert query._latest_dispatched_per_task() == {}
    replacement = tmp_path / 'replacement.jsonl'
    replacement.write_text('{"payload": {"assignee": "verify", "noise": "' + 'x' * 1000 + '"}, "task_id": "new", "type": "task.dispatched"}\n')
    replacement.replace(path)
    assert query._latest_dispatched_per_task() == {'new': 'verify'}
    path.write_text('')
    assert query._latest_dispatched_per_task() == {}
