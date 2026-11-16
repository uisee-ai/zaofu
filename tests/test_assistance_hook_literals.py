"""Assistance question text is data; shell composition remains guarded."""
import pytest
from zf.core.state.role_sessions import RoleSessionRegistry

from tests.test_hook_recv_codex import _invoke, _seed_claude_fanout_workdir


@pytest.mark.parametrize('extra,expected', [
    (" --question 'The tool rejected rm -rf; Path.write_text was not run.'", 0),
    (' --question "$(touch {root}/outside)"', 2),
    (" --question 'literal rm -rf' ; rm -rf {root}/outside", 2),
    (" --question 'literal rm -rf' > {root}/outside", 2),
    (" --question 'literal rm -rf' | tee {root}/outside", 2),
])
def test_assistance_request_uses_normal_hook_boundary(tmp_path, monkeypatch, extra, expected):
    state = tmp_path / '.zf'
    state.mkdir()
    workdir = _seed_claude_fanout_workdir(state)
    session = RoleSessionRegistry(state / 'role_sessions.yaml', project_root=str(tmp_path)).get_or_create('dev-1')
    command = (f'/opt/zaofu/bin/zf recover assistance request --state-dir {state} '
               '--operation-id op --attempt-id attempt --lease-id lease --actor dev-1 --owner run_manager'
               + extra.format(root=tmp_path))
    assert _invoke(state, event='codex.hook.pre_tool_use', backend='codex', payload={
        'session_id': str(session), 'cwd': str(workdir), 'tool_name': 'Bash',
        'tool_input': {'command': command}}, monkeypatch=monkeypatch) == expected
