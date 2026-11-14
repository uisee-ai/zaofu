import json
import time
from unittest.mock import Mock

from zf.core.state.role_sessions import RoleSessionRegistry
from zf.runtime.session_tailer_recovery import restore_preserved_session_tailers


def test_restores_dynamic_codex_lane_without_replaying_or_mutating_registry(tmp_path):
    registry = RoleSessionRegistry(tmp_path / 'roles.yaml', tmp_path)
    session_id = registry.get_or_create('prd-dev-lane-7', backend='codex')
    path = tmp_path / f'rollout-{session_id}.jsonl'
    path.write_text('{"type":"session_meta"}\n')
    registry.bind_codex_session('prd-dev-lane-7', str(session_id), session_path=path)
    before = registry.path.read_bytes()
    codex, claude = Mock(), Mock()

    restored = restore_preserved_session_tailers(registry, codex, claude)

    assert restored == ['prd-dev-lane-7']
    codex.tail.assert_called_once_with('prd-dev-lane-7', path, replay_existing=False)
    claude.tail.assert_not_called()
    assert registry.path.read_bytes() == before


def test_skips_missing_sessions_and_unknown_backends(tmp_path):
    registry = RoleSessionRegistry(tmp_path / 'roles.yaml', tmp_path)
    registry.get_or_create('pending', backend='codex')
    registry.get_or_create('mock', backend='mock')
    codex, claude = Mock(), Mock()
    assert restore_preserved_session_tailers(registry, codex, claude) == []
    codex.tail.assert_not_called()
    claude.tail.assert_not_called()


def test_dry_run_never_attaches(tmp_path):
    registry = Mock()
    assert restore_preserved_session_tailers(registry, Mock(), Mock(), dry_run=True) == []
    registry.all.assert_not_called()


def test_resumed_codex_telemetry_only_emits_new_lines_once(tmp_path):
    from zf.core.events.log import EventLog
    from zf.runtime.session_tailer import CodexSessionTailer

    registry = RoleSessionRegistry(tmp_path / 'roles.yaml', tmp_path)
    session_id = registry.get_or_create('prd-dev-lane-7', backend='codex')
    path = tmp_path / f'rollout-{session_id}.jsonl'

    def line(call_id):
        return json.dumps({'type': 'response_item', 'payload': {
            'type': 'function_call', 'name': 'exec_command',
            'call_id': call_id, 'arguments': '{"cmd":"true"}',
        }}) + '\n'

    path.write_text(line('old'))
    registry.bind_codex_session('prd-dev-lane-7', str(session_id), session_path=path)
    log = EventLog(tmp_path / 'events.jsonl')
    tailer = CodexSessionTailer(log)
    try:
        restore_preserved_session_tailers(registry, tailer, Mock())
        restore_preserved_session_tailers(registry, tailer, Mock())
        with path.open('a') as stream:
            stream.write(line('new'))
        deadline = time.monotonic() + 3
        while not log.read_all() and time.monotonic() < deadline:
            time.sleep(0.02)
    finally:
        tailer.stop()
    events = log.read_all()
    assert len(events) == 1
    assert events[0].type == 'agent.tool.use'
    assert events[0].actor == 'prd-dev-lane-7'
