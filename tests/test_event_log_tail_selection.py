from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import os

import pytest

from zf.core.events import log as log_module
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.security.signing import EventSigner
from zf.runtime.lifecycle_observation import LifecycleObservationMixin


def test_tail_reads_preserve_order_isolation_and_append_boundaries(tmp_path):
    log = EventLog(tmp_path / 'events.jsonl')
    rows = [ZfEvent(type='noise', payload={'nested': {'i': i}}) for i in range(4)]
    for row in rows:
        log.append(row)
    for _ in range(2):
        tail = log.read_days(limit=2)
        assert tail == rows[-2:]
        tail[0].payload['nested']['i'] = -1
        assert log.read_days() == rows
    extra = ZfEvent(type='selected', payload={'value': '完整正文'})
    line = extra.to_json()
    with log.path.open('a') as handle:
        handle.write(line[:30])
    assert log.read_days(limit=2) == rows[-2:]
    with log.path.open('a') as handle:
        handle.write(line[30:] + '\n')
    assert log.read_days(limit=2) == [rows[-1], extra]
    assert log.read_days(event_types={'selected'}, limit=1) == [extra]
    assert log.read_days(limit=0) == []
    with pytest.raises(ValueError):
        log.read_days(limit=-1)


def test_tail_keeps_global_selected_order_across_rotation(tmp_path):
    log = EventLog(tmp_path / 'events.jsonl')
    old = [ZfEvent(type='selected') for _ in range(3)]
    for row in old:
        log.append(row)
    assert log.read_days(limit=2) == old[-2:]
    archive = tmp_path / 'events'
    archive.mkdir()
    day = (datetime.now(timezone.utc) - timedelta(days=1)).date()
    log.path.rename(archive / f'{day.isoformat()}.jsonl')
    new = ZfEvent(type='selected')
    log.append(new)
    log.append(ZfEvent(type='noise'))
    assert log.read_days(1, event_types={'selected'}, limit=2) == [new]
    assert log.read_days(2, event_types={'selected'}, limit=2) == [old[-1], new]
    assert log.read_days(2, limit=3) == log.read_days(2)[-3:]
    assert log.read_days(2, limit=0) == []


def test_tail_reverifies_signed_content_with_unchanged_metadata(tmp_path):
    log = EventLog(tmp_path / 'events.jsonl', signer=EventSigner(b'test-key'))
    log.append(ZfEvent(type='noise', payload={'text': 'original'}))
    assert log.read_days(limit=1)[0].type == 'noise'
    stat = log.path.stat()
    log.path.write_text(log.path.read_text().replace('original', 'tampered'))
    os.utime(log.path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    assert [e.type for e in log.read_days(limit=1)] == ['event.malformed']


def test_periodic_drift_and_refresh_consume_only_full_last_fifty(tmp_path, monkeypatch):
    log = EventLog(tmp_path / 'events.jsonl')
    old = ZfEvent(type='worker.drift.detected', payload={
        'severity': 'high', 'recommended_action': 'refresh', 'affected_role': 'old'})
    log.append(old)
    for i in range(60):
        log.append(ZfEvent(type='noise', payload={'index': i}))
    copied = []
    original = log_module.deepcopy
    def copy(payload):
        copied.append(payload)
        return original(payload)
    monkeypatch.setattr(log_module, 'deepcopy', copy)
    reader = SimpleNamespace(event_log=log)
    refresh = LifecycleObservationMixin._recent_drift_refresh_scope
    for _ in range(2):
        copied.clear()
        assert refresh(reader) == (set(), False)
        assert len(copied) <= 50
    current = ZfEvent(type='worker.drift.detected', payload={
        'severity': 'high', 'recommended_action': 'refresh', 'affected_role': 'current'})
    log.append(current)
    copied.clear()
    assert refresh(reader) == ({'current'}, False)
    assert len(copied) <= 50
    observed = []
    def check(events, *, expected_roles):
        observed.extend(events)
        return []
    reader._active_drift_expected_roles = lambda: []
    reader._drift_detector = SimpleNamespace(check=check)
    copied.clear()
    LifecycleObservationMixin._check_drift(reader)
    assert len(copied) <= 50
    assert len(observed) == 50
    assert observed[-1] == {'type': current.type, 'task_id': '', 'actor': current.actor or ''}
    assert all(row['type'] == 'noise' for row in observed[:-1])


def test_tail_keeps_limit_when_rotation_races_incremental_read(tmp_path, monkeypatch):
    log = EventLog(tmp_path / 'events.jsonl')
    log.append(ZfEvent(type='old'))
    assert len(log.read_days(limit=2)) == 1
    log.append(ZfEvent(type='old.append'))
    original = EventLog._parse_tail
    replacement = [ZfEvent(type='new', payload={'i': i}) for i in range(4)]
    def rotate(self, path, offset, size):
        path.rename(path.with_suffix('.old'))
        path.write_text(''.join(row.to_json() + '\n' for row in replacement))
        return original(self, path, offset, size)
    monkeypatch.setattr(EventLog, '_parse_tail', rotate)
    assert log.read_days(limit=2) == replacement[-2:]
