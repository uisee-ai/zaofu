"""Editable legacy child results, authorized by the current ledger dispatch."""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

from zf.core.events.log import EventLog
from zf.core.state.atomic_io import atomic_write_text
from zf.runtime.fanout import FanoutManifestProjector
from zf.runtime.reader_fanout_recovery_snapshot import build_fanout_currentness


def _draft_path(state_dir: Path, run_id: str, label: str) -> Path:
    if not re.fullmatch(r'[A-Za-z0-9._-]+', run_id) or run_id in {'.', '..'}:
        raise ValueError('invalid fanout result attempt id')
    if label not in {'success', 'failure'}:
        raise ValueError('invalid fanout result label')
    return state_dir.resolve() / 'artifacts' / 'attempts' / run_id / 'completion-drafts' / f'{label}.json'


def prepare_fanout_result_draft(
    state_dir: Path, *, run_id: str, label: str, payload: dict,
) -> str:
    """Keep edits across briefing retries; only submission creates result truth."""
    path = _draft_path(state_dir, run_id, label)
    if path.resolve() != path:
        raise ValueError('fanout result draft cannot traverse symlinks')
    if not path.exists():
        atomic_write_text(path, json.dumps(payload, ensure_ascii=False, indent=2) + '\n')
    return str(path)


def is_authorized_fanout_result_draft(
    state_dir: Path, event_log: EventLog, *, role_instance: str, target: Path,
) -> bool:
    """Permit only two existing draft files of the actor's latest live dispatch."""
    actual = Path(os.path.abspath(target.expanduser()))
    if actual.resolve() != actual or not actual.is_file():
        return False
    events = event_log.read_all()
    dispatch = next((event for event in reversed(events)
        if event.type in {'fanout.child.dispatched', 'fanout.synth.dispatched', 'task.dispatched'}
        and role_instance in {str(event.payload.get(key) or '')
            for key in ('role_instance', 'assignee', 'role')}), None)
    if not role_instance or dispatch is None or dispatch.type != 'fanout.child.dispatched':
        return False
    payload = dispatch.payload
    fanout_id = str(payload.get('fanout_id') or '')
    run_id = str(payload.get('run_id') or '')
    child_id = str(payload.get('child_id') or '')
    if not fanout_id or not child_id or not run_id:
        return False
    try:
        expected = {_draft_path(state_dir, run_id, label) for label in ('success', 'failure')}
    except ValueError:
        return False
    if actual not in expected:
        return False
    if build_fanout_currentness(events).get(fanout_id, ('', ''))[0]:
        return False
    manifest = FanoutManifestProjector(state_dir).rebuild(fanout_id, events)
    terminal = {'completed', 'failed', 'cancelled', 'timed_out', 'corrected_passed'}
    if manifest.get('status') in terminal or manifest.get('aggregate', {}).get('status') in terminal:
        return False
    return any(child.get('child_id') == child_id
        and child.get('role_instance') == role_instance
        and child.get('run_id') == run_id
        and child.get('status') == 'dispatched'
        for child in manifest.get('children', []))
