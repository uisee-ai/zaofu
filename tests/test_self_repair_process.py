from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from zf.runtime.self_repair_process import LAUNCH_SCHEMA, run_launch


def _launch(tmp_path, *, timeout_seconds=30):
    launch = tmp_path / "launch.json"
    launch.write_text(json.dumps({
        "schema_version": LAUNCH_SCHEMA,
        "operation_id": "self-repair-op-1",
        "backend": "mock",
        "argv": ["mock-provider", "--run"],
        "cwd": str(tmp_path),
        "timeout_seconds": timeout_seconds,
        "repair_contract_digest": "a" * 64,
    }), encoding="utf-8")
    return launch, tmp_path / "result.json"


@pytest.mark.parametrize(
    ("returncode", "expected_status"),
    [(0, "completed"), (7, "failed")],
)
def test_process_wrapper_persists_exit_status(
    tmp_path,
    monkeypatch,
    returncode,
    expected_status,
):
    launch, result_path = _launch(tmp_path)
    monkeypatch.setattr(
        "zf.runtime.self_repair_process.run_repair_command",
        lambda *args, **kwargs: SimpleNamespace(returncode=returncode, cleanup_receipt={"cleanup":"complete"}),
    )

    result = run_launch(launch, result_path)

    assert result["status"] == expected_status
    assert result["returncode"] == returncode
    assert json.loads(result_path.read_text(encoding="utf-8")) == result


def test_process_wrapper_persists_timeout(tmp_path, monkeypatch):
    launch, result_path = _launch(tmp_path, timeout_seconds=1)

    def timeout(*args, **kwargs):
        error = subprocess.TimeoutExpired(args[0], timeout=1)
        error.cleanup_receipt = {"cleanup": "complete"}
        raise error

    monkeypatch.setattr(
        "zf.runtime.self_repair_process.run_repair_command",
        timeout,
    )

    result = run_launch(launch, result_path)

    assert result["status"] == "timeout"
    assert result["returncode"] == 124
    assert result["reason"] == "provider_timeout"


def test_repair_provider_and_its_tests_do_not_inherit_live_project(tmp_path, monkeypatch):
    live = tmp_path / 'live'
    live.mkdir()
    sentinel = live / 'events.jsonl'
    sentinel.write_text('original ledger\n')
    inherited = {
        'ZF_PROJECT_ROOT': str(live), 'ZF_STATE_DIR': str(live),
        'ZF_WORKSPACE_HOME': str(live), 'ZF_WEB_ACTION_TOKEN': 'must-not-inherit',
        'ZF_TASK_ID': 'live-task', 'ZF_CLI_CMD': 'live-cli',
        'TMUX': 'live-socket', 'TMUX_TMPDIR': str(live),
        'GIT_DIR': str(live), 'PYTHONPATH': str(live),
        'PYTEST_ADDOPTS': '--invalid-inherited-option',
    }
    for key, value in inherited.items():
        monkeypatch.setenv(key, value)
    probe = tmp_path / 'provider.py'
    probe.write_text('''import json, os, subprocess, sys
from pathlib import Path
env = dict(os.environ)
assert {key for key in env if key.startswith('ZF_')} == {'ZF_WORKSPACE_HOME', 'ZF_PYTHON'}
assert not any(key.startswith(('TMUX', 'GIT_')) for key in env)
assert 'PYTEST_ADDOPTS' not in env
assert env['PYTHONPATH'] == 'src'
assert env['ZF_PYTHON'] == sys.executable
workspace = Path(env['ZF_WORKSPACE_HOME'])
assert workspace.is_dir()
subprocess.run([sys.executable, '-c', "import os; from pathlib import Path; Path(os.environ['ZF_WORKSPACE_HOME'], 'test-write').write_text('isolated')"], check=True)
Path('observed.json').write_text(json.dumps({'workspace': str(workspace), 'nested_write': (workspace / 'test-write').read_text()}))
''')
    launch, result_path = _launch(tmp_path)
    manifest = json.loads(launch.read_text())
    manifest['argv'] = [sys.executable, str(probe)]
    launch.write_text(json.dumps(manifest))

    result = run_launch(launch, result_path)

    assert result['status'] == 'completed', result
    observed = json.loads((tmp_path / 'observed.json').read_text())
    assert observed['nested_write'] == 'isolated'
    assert not Path(observed['workspace']).exists()
    assert sentinel.read_text() == 'original ledger\n'
    assert sorted(p.name for p in live.iterdir()) == ['events.jsonl']
