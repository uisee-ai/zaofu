from pathlib import Path
import subprocess
from unittest.mock import MagicMock, patch

from zf.autoresearch.resident import plan_resident_actions
from zf.cli.main import main
from zf.core.events import EventLog, ZfEvent
from tests.test_source_repair_apply import _git


def test_resident_command_consumes_actual_accepted_selector_once(tmp_path, monkeypatch):
    root = tmp_path / 'harness'
    root.mkdir()
    _git(root, 'init', '-q'); _git(root, 'config', 'user.name', 'Test'); _git(root, 'config', 'user.email', 'test@example.invalid')
    (root / 'README.md').write_text('fixture\n')
    _git(root, 'add', 'README.md'); _git(root, 'commit', '-qm', 'chore: fixture')
    state = tmp_path / 'custom-state'
    log = EventLog(state / 'events.jsonl')
    for kind, candidate in [('run.manager.repair.accepted', 'accepted'), ('autoresearch.repair.dispatch_requested', 'unaccepted')]:
        log.append(ZfEvent(type=kind, origin='kernel', payload={
            'fingerprint': candidate, 'attempt': 1, 'candidate_id': candidate, 'candidate_path': '',
            'repair_task_payload': {'contract': {'scope': ['src/zf/**'], 'verification_commands': ['git diff --check']}},
        }))
    actions = plan_resident_actions(state_dir=state, worktree_root=tmp_path / 'resident', output_root=tmp_path / 'out',
        self_repair_consumer=True, self_repair_spawn=True, self_repair_backend='codex')
    action = next(a for a in actions if a.kind == 'repair_dispatch')
    assert '--accepted-only' in action.command
    monkeypatch.setattr('zf.runtime.self_repair_runner.tempfile.gettempdir', lambda: str(tmp_path))
    original_popen = subprocess.Popen
    spawned = []
    def launch(argv, *args, **kwargs):
        if 'zf.runtime.self_repair_process' in argv:
            spawned.append(argv)
            return MagicMock(pid=1234)
        return original_popen(argv, *args, **kwargs)
    with patch('zf.runtime.self_repair_runner.subprocess.Popen', side_effect=launch):
        assert main([*action.command[3:], '--harness-root', str(root)]) == 0
        assert main([*action.command[3:], '--harness-root', str(root)]) == 0
    assert len(spawned) == 1
    dispatched = [e for e in EventLog(state / 'events.jsonl').read_all() if e.type == 'autoresearch.repair.dispatched']
    assert len(dispatched) == 1
    assert dispatched[0].payload['candidate_id'] == 'accepted'
