"""Real child initialization must never reset the invoking project's state."""

import os
import subprocess
import sys

import pytest

from zf.autoresearch import orchestrator
from tests.e2e import run_mixed


@pytest.mark.parametrize("environment", [orchestrator._env, run_mixed._subprocess_env])
def test_experiment_init_preserves_parent_state(tmp_path, monkeypatch, environment):
    parent = tmp_path / "parent"
    state = parent / "parent-state"
    state.mkdir(parents=True)
    (parent / "zf.yaml").write_text("project:\n  name: parent\n  state_dir: parent-state\n")
    # Deliberately distinctive bytes; an accidental force-init must be detected.
    ledger = state / "events.jsonl"
    tasks = state / "kanban.json"
    ledger.write_text('{"type":"parent.sentinel"}\n')
    tasks.write_text('[{"id":"PARENT-TASK"}]\n')
    before = [ledger.read_bytes(), tasks.read_bytes()]
    child = tmp_path / "child"
    child.mkdir()
    (child / "zf.yaml").write_text("project:\n  name: child\n  state_dir: child-state\n")
    monkeypatch.setenv("ZF_PROJECT_ROOT", str(parent))
    monkeypatch.setenv("ZF_STATE_DIR", str(state))
    monkeypatch.setenv("ISOLATION_TEST_PROVIDER_SETTING", "keep-me")
    env = environment()
    result = subprocess.run(
        [sys.executable, "-m", "zf.cli.main", "init", "--force", "--skip-instruction-docs", "--no-git-hooks"],
        cwd=child, env=env, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert [ledger.read_bytes(), tasks.read_bytes()] == before
    assert (child / "child-state" / "events.jsonl").is_file()
    assert env["ISOLATION_TEST_PROVIDER_SETTING"] == "keep-me"
    assert os.environ["ZF_STATE_DIR"] == str(state)
