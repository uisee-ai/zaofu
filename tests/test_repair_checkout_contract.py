from pathlib import Path
import shlex
import sys

import pytest
from zf.runtime.repair_dispatch import RepairRequest, write_repair_contract


def test_frozen_contract_rebinds_source_paths_to_repair_checkout(tmp_path):
    source = tmp_path / 'original source'
    source.mkdir()
    req = RepairRequest(fingerprint='f', attempt=0, candidate_id='c', candidate_path='evidence',
        repair_task_payload={'contract': {'scope': [str(source / 'src/zf/runtime/host_verification.py')],
        'verification': f'cd {shlex.quote(str(source))} && .venv/bin/python -m pytest -q tests/test_host_verification.py'}})
    contract, _ = write_repair_contract(tmp_path / 'state', req, base_commit='base', created_by='test', source_root=source)
    assert contract['scope'] == ['src/zf/runtime/host_verification.py']
    command = contract['verification_commands'][0]
    assert str(source) not in command
    assert command == f'env PYTHONPATH=src {shlex.quote(sys.executable)} -m pytest -q tests/test_host_verification.py'


@pytest.mark.parametrize('scope', ['/etc/passwd', '../outside.py'])
def test_repair_scope_outside_source_is_rejected_before_freeze(tmp_path, scope):
    req = RepairRequest(fingerprint='f', attempt=0, candidate_id='c', candidate_path='evidence', repair_task_payload={'contract': {'scope': [scope]}})
    with pytest.raises(ValueError, match='scope'):
        write_repair_contract(tmp_path / 'state', req, base_commit='base', created_by='test', source_root=tmp_path / 'source')
    assert not list((tmp_path / 'state').rglob('*.json'))


def test_shared_python_verifies_isolated_source_not_original(tmp_path):
    import subprocess
    source = tmp_path / 'source'
    worktree = tmp_path / 'repair'
    source.mkdir()
    (worktree / 'src').mkdir(parents=True)
    (worktree / 'tests').mkdir()
    (worktree / 'src/repair_probe.py').write_text('VALUE = "isolated"\n')
    (worktree / 'tests/test_probe.py').write_text('import repair_probe\ndef test_import():\n    assert repair_probe.VALUE == "isolated"\n')
    req = RepairRequest(fingerprint='f', attempt=0, candidate_id='c', candidate_path='evidence', repair_task_payload={'contract': {
        'scope': ['src/**', 'tests/**'], 'verification': f'cd {source} && .venv/bin/python -m pytest -q -c /dev/null --no-cov tests/test_probe.py'}})
    contract, _ = write_repair_contract(tmp_path / 'state', req, base_commit='base', created_by='test', source_root=source)
    result = subprocess.run(shlex.split(contract['verification_commands'][0]), cwd=worktree, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    assert '1 passed' in result.stdout


def test_scope_symlink_escape_is_rejected(tmp_path):
    from zf.runtime.repair_checkout_contract import bind_checkout_contract
    source = tmp_path / "source"
    source.mkdir()
    (source / "escape").symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(ValueError, match="scope"):
        bind_checkout_contract({"scope": ["escape/outside.py"],
            "verification_instruction": "", "verification_commands": []}, source)


def test_untranslated_original_checkout_command_is_rejected(tmp_path):
    from zf.runtime.repair_checkout_contract import bind_checkout_contract
    source = tmp_path / "source"
    with pytest.raises(ValueError, match="isolated checkout"):
        bind_checkout_contract({"scope": ["src/fix.py"],
            "verification_instruction": f"pytest {source}/tests/test_fix.py",
            "verification_commands": []}, source)
