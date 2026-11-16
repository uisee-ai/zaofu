"""Independent repair verification consumes the frozen checkout command contract."""
import shlex
import sys

import pytest

from zf.runtime.repair_checkout_contract import bind_checkout_contract
from zf.runtime.run_manager_repair_validation import execute_repair_verification_plan


def test_independent_validator_runs_frozen_shared_python_in_candidate(tmp_path):
    root = tmp_path / 'repair'
    (root / 'src').mkdir(parents=True)
    (root / 'tests').mkdir()
    (root / 'src/candidate_probe.py').write_text('VALUE = "candidate"\n')
    (root / 'tests/test_candidate.py').write_text(
        'import candidate_probe\ndef test_candidate():\n    assert candidate_probe.VALUE == "candidate"\n')
    contract = bind_checkout_contract({'scope': ['src/**', 'tests/**'],
        'verification_instruction': 'pytest -q -c /dev/null --no-cov tests/test_candidate.py',
        'verification_commands': []}, tmp_path / 'original')
    result = execute_repair_verification_plan(worktree=str(root), verification_plan=[{
        'kind': 'repair_contract', 'command': contract['verification_commands'][0]}])
    assert result['ok'], result
    assert '1 passed' in result['results'][0]['stdout_tail']


@pytest.mark.parametrize('command', [
    'env PYTHONPATH=/outside python -m pytest tests/test_probe.py',
    'env PYTHONPATH=src /outside/python -m pytest tests/test_probe.py',
    f'env PYTHONPATH=src {shlex.quote(sys.executable)} -c "raise RuntimeError()"',
])
def test_independent_validator_rejects_unbound_interpreter_or_environment(tmp_path, command):
    result = execute_repair_verification_plan(worktree=str(tmp_path), verification_plan=[{'command': command}])
    assert not result['ok']
    assert result['reason'] == 'command_not_allowlisted'
