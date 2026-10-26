from pathlib import Path
import subprocess

import pytest

from zf.core.config.schema import QualityGateConfig
from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.core.task.schema import Task, TaskContract
from zf.core.task.store import TaskStore
from tests.test_candidates import (
    _approve, _init_repo, _rebuilder, _record_task_ref, _state, _task_commit,
)


@pytest.mark.parametrize("timeout,expires", [(90, False), (1200, False), (1200, True), (None, False)])
def test_candidate_rebuild_preserves_command_timeout(tmp_path: Path, monkeypatch, timeout, expires):
    _init_repo(tmp_path)
    state_dir, config, log = _state(tmp_path)
    commit = _task_commit(tmp_path, branch="worker/TASK-1", file_name="a.txt",
                          content="delivered\n", message="deliver TASK-1")
    _record_task_ref(tmp_path, state_dir, config, task_id="TASK-1",
                     commit=commit, branch="worker/TASK-1")
    contract = TaskContract()
    if timeout is None:
        config.quality_gates = {"legacy": QualityGateConfig(required_checks=["contract-check"])}
    else:
        contract.validation = {"commands": [{
            "id": "check", "command": "contract-check", "timeout_seconds": timeout,
        }]}
    TaskStore(state_dir / "kanban.json").add(Task(
        id="TASK-1", title="task", key="F-11111111:TASK-1", contract=contract,
    ))
    log.append(ZfEvent(type="task.created", actor="zf-cli", task_id="TASK-1",
                      payload={"feature_id": "F-11111111"}))
    _approve(log, "TASK-1")
    actual = []
    real_run = subprocess.run

    def run(command, *args, **kwargs):
        if command != "contract-check":
            return real_run(command, *args, **kwargs)
        actual.append(kwargs["timeout"])
        if expires:
            raise subprocess.TimeoutExpired(command, kwargs["timeout"], output="partial output")
        return subprocess.CompletedProcess(command, 0, "checked", "")

    monkeypatch.setattr(subprocess, "run", run)
    result = _rebuilder(tmp_path, state_dir, config, log).rebuild(
        "F-11111111", event_writer=EventWriter(log),
    )
    expected = timeout if timeout is not None else 300
    assert actual == [expected]
    assert result is not None
    assert result.status == ("quality_failed" if expires else "updated")
    gate = "legacy" if timeout is None else "task_contract:TASK-1:check"
    evidence = result.payload["quality"]["gate_checks"][gate][0]
    assert evidence["timeout_seconds"] == expected
    if expires:
        assert evidence["timed_out"] is True
        assert f"timed out after {expected}s" in result.payload["quality"]["failure_details"][gate][0]
        assert not any(event.type == "candidate.quality.passed" for event in log.read_all())
