"""The executing resident owns exclusion even if its launcher has exited."""
from __future__ import annotations

from pathlib import Path
import subprocess
import sys
import time

import pytest

_SCRIPT = r'''
from pathlib import Path
from types import SimpleNamespace
import sys, time
from zf.autoresearch import resident_cli
from zf.cli.autoresearch import _resident
state, marker = map(Path, sys.argv[1:3])
hold, execute = sys.argv[3:5]
def tick(**kwargs):
    marker.write_text('executed')
    if hold == 'hold':
        while not kwargs['should_stop']():
            time.sleep(.02)
    return []
resident_cli.run_resident_once = tick
args = SimpleNamespace(state_dir=state, output_root=None, worktree_root=state/'worktrees',
 interval_seconds=.1, max_actions_per_tick=1, execute=execute=='yes',
 self_repair_consumer=False, self_repair_spawn=False, self_repair_backend='', watch=False)
raise SystemExit(_resident(args))
'''


def command(state: Path, marker: Path, *, hold: bool = False, execute: bool = True):
    return [sys.executable, '-c', _SCRIPT, str(state), str(marker),
            'hold' if hold else 'once', 'yes' if execute else 'no']


@pytest.mark.parametrize('release', ['terminate', 'kill'])
def test_actual_cli_serializes_execute_until_owner_exits(tmp_path: Path, release: str):
    state = tmp_path / 'custom-state'
    owner_marker = tmp_path / 'owner'
    owner = subprocess.Popen(command(state, owner_marker, hold=True),
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        deadline = time.monotonic() + 10
        while not owner_marker.exists() and owner.poll() is None and time.monotonic() < deadline:
            time.sleep(.02)
        assert owner_marker.exists(), owner.communicate(timeout=1)
        duplicate_marker = tmp_path / 'duplicate'
        duplicate = subprocess.run(command(state, duplicate_marker), capture_output=True,
                                   text=True, timeout=10)
        assert duplicate.returncode != 0
        assert not duplicate_marker.exists(), 'duplicate executed while owner still alive'
        assert 'already running' in duplicate.stderr
        # Read-only inspection and another configured state do not share the gate.
        for other_state, execute in [(state, False), (tmp_path / 'other-state', True)]:
            marker = tmp_path / ('preview' if not execute else 'other')
            result = subprocess.run(command(other_state, marker, execute=execute),
                                    capture_output=True, text=True, timeout=10)
            assert result.returncode == 0, result.stderr
            assert marker.exists()
        getattr(owner, release)()
        owner.communicate(timeout=10)
        resumed = subprocess.run(command(state, duplicate_marker), capture_output=True,
                                 text=True, timeout=10)
        assert resumed.returncode == 0, resumed.stderr
        assert duplicate_marker.exists()
    finally:
        if owner.poll() is None:
            owner.kill()
        owner.communicate(timeout=10)
