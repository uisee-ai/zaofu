import pytest

from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.security.signing import EventSigner


def test_window_reads_decode_only_appended_complete_rows(tmp_path, monkeypatch):
    log = EventLog(tmp_path / 'events.jsonl')
    first = ZfEvent(type='task.created')
    log.append(first)
    assert [e.id for e in log.read_days()] == [first.id]
    decoded = []
    original = log._decode
    def decode(line):
        decoded.append(line)
        return original(line)
    monkeypatch.setattr(log, '_decode', decode)
    assert [e.id for e in log.read_days()] == [first.id]
    assert not decoded
    second = ZfEvent(type='dev.blocked')
    row = second.to_json()
    with log.path.open('a') as out:
        out.write(row[:20])
    assert [e.id for e in log.read_days()] == [first.id]
    with log.path.open('a') as out:
        out.write(row[20:] + '\n')
    assert [e.id for e in log.read_days()] == [first.id, second.id]
    assert len(decoded) == 1


@pytest.mark.parametrize('change', ['rotate', 'replace_same_size', 'truncate', 'delete'])
def test_window_cache_rechecks_file_identity_and_membership(tmp_path, change):
    path = tmp_path / 'events.jsonl'
    log = EventLog(path)
    first = ZfEvent(type='old', id='evt-aaa')
    second = ZfEvent(type='new', id='evt-bbb', ts=first.ts)
    log.append(first)
    log.read_days(2)
    if change == 'rotate':
        archive = tmp_path / 'events'
        archive.mkdir()
        path.rename(archive / '2026-11-04.jsonl')
        log.append(second)
        assert [e.id for e in log.read_days(2)] == [first.id, second.id]
        assert [e.id for e in log.read_days()] == [second.id]
    elif change == 'replace_same_size':
        assert len(first.to_json()) == len(second.to_json())
        replacement = tmp_path / 'new.jsonl'
        replacement.write_text(second.to_json() + '\n')
        replacement.replace(path)
        assert [e.id for e in log.read_days()] == [second.id]
    elif change == 'truncate':
        path.write_text('')
        assert log.read_days() == []
    else:
        path.unlink()
        assert log.read_days() == []


def test_window_cache_preserves_signature_and_unsigned_policy(tmp_path):
    path = tmp_path / 'events.jsonl'
    trusted = EventLog(path, signer=EventSigner(b'test-key'))
    trusted.append(ZfEvent(type='signed'))
    assert [e.type for e in trusted.read_days()] == ['signed']
    assert EventLog(path, signer=EventSigner(b'wrong-key')).read_days() == []
    with path.open('a') as out:
        out.write(ZfEvent(type='plain').to_json() + '\n')
    assert [e.type for e in trusted.read_days()] == ['signed', 'event.malformed']
    tolerant = EventLog(path, signer=EventSigner(b'test-key'), allow_unsigned=True)
    assert [e.type for e in tolerant.read_days()] == ['signed', 'plain']
