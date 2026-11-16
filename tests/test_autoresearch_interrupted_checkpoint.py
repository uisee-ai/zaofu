"""An interrupted diagnosis is retained and explicitly closed, never passed."""
import os
from pathlib import Path

from zf.core.events import EventLog, EventWriter
from zf.autoresearch.interrupted_checkpoint import checkpoint_started, reconcile_interrupted


def test_dead_owner_is_reconciled_once_with_checkpoint_and_preserved_worktree(tmp_path, monkeypatch):
    writer = EventWriter(EventLog(tmp_path / 'state/events.jsonl'))
    worktree = tmp_path / 'repair'
    worktree.mkdir()
    (worktree / 'candidate.py').write_text('unfinished patch')
    started = checkpoint_started(writer, state_dir=tmp_path / 'state', request_id='r1',
                                  command=['zf', 'autoresearch', 'loop'], worktree=worktree)
    assert started.payload['checkpoint_ref']['sha256']
    monkeypatch.setattr('zf.autoresearch.interrupted_checkpoint.owner_gone', lambda p: True)
    assert reconcile_interrupted(writer, state_dir=tmp_path / 'state') == 1
    assert reconcile_interrupted(writer, state_dir=tmp_path / 'state') == 0
    failures = [e for e in writer.event_log.read_all() if e.type == 'autoresearch.loop.failed']
    assert len(failures) == 1 and failures[0].payload['reason'] == 'diagnosis_owner_interrupted'
    assert failures[0].payload['checkpoint_ref'] == started.payload['checkpoint_ref']
    assert (worktree / 'candidate.py').read_text() == 'unfinished patch'


def test_live_or_unknown_owner_is_not_interrupted(tmp_path):
    writer = EventWriter(EventLog(tmp_path / 'events.jsonl'))
    event = checkpoint_started(writer, state_dir=tmp_path, request_id='r1', command=['zf'], worktree=tmp_path)
    assert event.payload['owner_pid'] == os.getpid()
    assert reconcile_interrupted(writer, state_dir=tmp_path) == 0
