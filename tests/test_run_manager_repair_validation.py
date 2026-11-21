"""Independent repair verification consumes the frozen checkout command contract."""
import shlex
import sys

import pytest

from zf.runtime.repair_checkout_contract import bind_checkout_contract
from zf.runtime.run_manager_repair_validation import execute_repair_verification_plan


@pytest.mark.parametrize('entrypoint, frozen_legacy', [
    ('pytest', False), ('.venv/bin/pytest', False), ('.venv/bin/pytest', True),
    ('PYTHONPATH=src uv run pytest', False),
    ('PYTHONPATH=src uv run pytest', True),
    ('env PYTHONPATH=src uv run pytest', True),
    ('uv run pytest', True),
])
def test_independent_validator_runs_frozen_shared_python_in_candidate(tmp_path, entrypoint, frozen_legacy):
    root = tmp_path / 'repair'
    (root / 'src').mkdir(parents=True)
    (root / 'tests').mkdir()
    (root / 'src/candidate_probe.py').write_text('VALUE = "candidate"\n')
    (root / 'tests/test_candidate.py').write_text(
        'import candidate_probe\ndef test_candidate():\n    assert candidate_probe.VALUE == "candidate"\n')
    local_bin = root / '.venv/bin'
    local_bin.mkdir(parents=True)
    local_pytest = local_bin / 'pytest'
    local_pytest.write_text('#!/bin/sh\ntouch untrusted-entrypoint-ran\nexit 93\n')
    local_pytest.chmod(0o755)
    command = f'{entrypoint} -q -c /dev/null --no-cov tests/test_candidate.py'
    contract = bind_checkout_contract({'scope': ['src/**', 'tests/**'],
        'verification_instruction': 'pytest -q -c /dev/null --no-cov tests/test_candidate.py',
        'verification_commands': [command]}, tmp_path / 'original')
    result = execute_repair_verification_plan(worktree=str(root), verification_plan=[{
        'kind': 'repair_contract', 'command': command if frozen_legacy else contract['verification_commands'][0]}])
    assert result['ok'], result
    assert '1 passed' in result['results'][0]['stdout_tail']
    assert not (root / 'untrusted-entrypoint-ran').exists()


@pytest.mark.parametrize('command', [
    'env PYTHONPATH=/outside python -m pytest tests/test_probe.py',
    'env PYTHONPATH=src /outside/python -m pytest tests/test_probe.py',
    f'env PYTHONPATH=src {shlex.quote(sys.executable)} -c "raise RuntimeError()"',
    '.venv/bin/pytest-other tests/test_probe.py',
    '/outside/pytest tests/test_probe.py',
    'PYTHONPATH=/outside uv run pytest tests/test_probe.py',
    'PYTHONPATH=src uv run --with arbitrary pytest tests/test_probe.py',
    'PYTHONPATH=src uv run pytest tests/test_probe.py && touch unauthorized',
    'LD_PRELOAD=/outside.so uv run pytest tests/test_probe.py',
    "PYTHONPATH=src uv run pytest 'unterminated",
])
def test_independent_validator_rejects_unbound_interpreter_or_environment(tmp_path, command):
    result = execute_repair_verification_plan(worktree=str(tmp_path), verification_plan=[{'command': command}])
    assert not result['ok']
    assert result['reason'] == 'command_not_allowlisted'


@pytest.mark.parametrize('mode', ['plan', 'run'])
def test_impact_validation_uses_own_interpreter_without_path_python(tmp_path, monkeypatch, mode):
    (tmp_path / 'scripts').mkdir()
    (tmp_path / 'scripts/dev-verify.py').write_text('import sys\nprint(sys.executable)\n')
    monkeypatch.setenv('PATH', '/nonexistent')
    result = execute_repair_verification_plan(worktree=str(tmp_path), verification_plan=[{
        'command': f'python scripts/dev-verify.py {mode} --base abc123'}])
    assert result['ok']
    assert sys.executable in result['results'][0]['stdout_tail']


def test_required_manual_review_is_not_a_pass_without_evidence(tmp_path):
    result = execute_repair_verification_plan(worktree=str(tmp_path), verification_plan=[{
        'kind': 'docs_review', 'command': 'review changed markdown', 'required': True}])
    assert not result['ok']
    assert result['reason'] == 'manual_review_evidence_required'
