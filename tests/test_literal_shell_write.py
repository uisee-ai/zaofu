"""Quoted document bodies are data; only proven write targets bypass heuristics."""
import pytest
from tests.test_hook_recv_codex import _invoke, _seed_claude_fanout_workdir
from zf.cli.literal_shell_write import literal_shell_write_targets
from zf.core.events import EventLog


@pytest.mark.parametrize('case', ['inside', 'outside', 'symlink', 'unquoted', 'execute', 'tail', 'substitution', 'mkdir_escape', 'brace_escape'])
def test_actual_hook_handles_literal_heredoc_without_weakening_escape_guard(tmp_path, monkeypatch, case):
    state = tmp_path / 'state';state.mkdir()
    workdir = _seed_claude_fanout_workdir(state)
    target = 'artifacts/build.py'
    outside = tmp_path / 'escape.py'
    if case == 'outside': target = str(outside)
    if case == 'symlink':
        (workdir / 'artifacts').symlink_to(tmp_path, target_is_directory=True)
    body = f'from pathlib import Path\nSOURCE="{tmp_path}/requirements.md"\nPath("own.json").write_text(SOURCE)'
    prefix = 'mkdir -p artifacts\n'
    command = f"{prefix}cat > {target} <<'PY'\n{body}\nPY\n"
    if case == 'unquoted': command = command.replace("<<'PY'", '<<PY')
    if case == 'execute': command = f"python <<'PY'\nfrom pathlib import Path\nPath('{outside}').write_text('bad')\nPY\n"
    if case == 'tail': command += f'echo bad > {outside}\n'
    if case == 'substitution': command = command.replace(target, f'$(echo {outside})', 1)
    if case == 'mkdir_escape': command = command.replace('mkdir -p artifacts', f'mkdir -p {tmp_path}/outside')
    if case == 'brace_escape': command = command.replace('mkdir -p artifacts', f'mkdir -p {{artifacts,{tmp_path}/outside}}')
    code = _invoke(state, event='claude.hook.pre_tool_use', backend='claude-code',
        payload={'session_id': '', 'cwd': str(workdir), 'hook_event_name': 'PreToolUse',
                 'tool_name': 'Bash', 'tool_input': {'command': command}}, monkeypatch=monkeypatch)
    assert code == (0 if case == 'inside' else 2)
    assert bool([e for e in EventLog(state/'events.jsonl').read_all() if e.type == 'worker.scope_write.rejected']) == (case != 'inside')


@pytest.mark.parametrize('command', ["cat > a <<'X'\nx\nX\nrm /root/a\nX", "cat > a <<X\nx\nX", "cat > a <<'X' | bash\nx\nX", "cat > a <<'X'\nx", "mkdir -p a; rm b\ncat > a <<'X'\nx\nX"])
def test_unrecognized_shell_never_enters_literal_exemption(command):
    assert literal_shell_write_targets(command) == []
