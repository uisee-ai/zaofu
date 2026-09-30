"""Offline workload/oracle controls, using real short CPU work and subprocesses."""

from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys
import time

import pytest

from tests.conformance.test_zaofu_testkit_s3_blocking_dispatch import _blocking_runtime_at
from tests.testkit.fixtures.chunked_workload import run
from tests.testkit.native_long_task import NativeLongTask, checkpoint_identity, validate_chunks


SCRIPT = Path(__file__).parent / "testkit/fixtures/chunked_workload.py"


def command(seconds=0.01, action="run"):
    return [sys.executable, str(SCRIPT), action, "--seed", "test-seed", "--seconds", str(seconds)]


def test_real_subprocess_interruption_resumes_without_rewriting_checkpoint(tmp_path):
    process = subprocess.Popen(command(0.2), cwd=tmp_path, stdout=subprocess.DEVNULL)
    root = tmp_path / "long-task"
    try:
        deadline = time.monotonic() + 10
        while True:
            assert process.poll() is None, "workload finished before interruption"
            path = root / "progress.json"
            progress = json.loads(path.read_text()) if path.exists() else {}
            if progress.get("index") == 1:
                break
            assert time.monotonic() < deadline
            time.sleep(0.001)
        first = (root / "chunk-0.json").read_bytes()
        identity = checkpoint_identity(root / "chunk-0.json")
        process.kill()
        assert process.wait(timeout=5) < 0
        assert not (root / "chunk-1.json").exists()
        assert checkpoint_identity(root / "chunk-0.json") == identity
        # An uncommitted temporary write is not a checkpoint or successful output.
        (root / "chunk-1.tmp").write_text("incomplete")
        resumed = subprocess.run(command(0.2), cwd=tmp_path, capture_output=True, timeout=10)
        assert resumed.returncode == 0, resumed.stderr
        chunks = [(root / f"chunk-{i}.json").read_bytes() for i in range(3)]
        rows = validate_chunks(chunks, "test-seed", 0.2)
        assert chunks[0] == first and checkpoint_identity(root / "chunk-0.json") == identity
        assert rows[0]["invocation"] == progress["invocation"]
        assert rows[1]["invocation"] == rows[2]["invocation"] != rows[0]["invocation"]
        assert not (root / "chunk-1.tmp").exists()
        before = [checkpoint_identity(root / f"chunk-{i}.json") for i in range(3)]
        assert subprocess.run(command(0.2), cwd=tmp_path, capture_output=True, timeout=5).returncode == 0
        assert [checkpoint_identity(root / f"chunk-{i}.json") for i in range(3)] == before
        assert subprocess.run(command(0.2, "verify"), cwd=tmp_path,
                              capture_output=True, timeout=5).returncode == 0
    finally:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=5)


@pytest.fixture
def completed(tmp_path):
    root = tmp_path / "long-task"
    run(root, "test-seed", 0.01)
    return root


@pytest.mark.parametrize("field,value", [
    ("seed", "foreign"), ("index", 2), ("digest", "0" * 64), ("passes", 0),
    ("passes", True), ("cpu_seconds", 0), ("cpu_seconds", float("nan")),
    ("cpu_seconds", float("inf")), ("wall_seconds", 0), ("pid", 0), ("invocation", ""),
])
def test_both_oracles_reject_corrupt_checkpoint_without_overwriting(completed, field, value):
    file = completed / "chunk-0.json"
    row = json.loads(file.read_text())
    row[field] = value
    file.write_text(json.dumps(row))
    before = {p.name: p.read_bytes() for p in completed.iterdir()}
    with pytest.raises(AssertionError):
        validate_chunks([(completed / f"chunk-{i}.json").read_bytes() for i in range(3)], "test-seed", 0.01)
    with pytest.raises(AssertionError):
        run(completed, "test-seed", 0.01)
    assert {p.name: p.read_bytes() for p in completed.iterdir()} == before


def test_missing_checkpoint_is_not_completed_and_gap_is_rejected(completed):
    (completed / "chunk-1.json").unlink()
    with pytest.raises(AssertionError, match="checkpoint gap"):
        run(completed, "test-seed", 0.01)
    with pytest.raises(AssertionError):
        validate_chunks([(completed / "chunk-0.json").read_bytes()], "test-seed", 0.01)


@pytest.mark.parametrize("seconds", [0, -1, 61, "nan", "inf"])
def test_cli_rejects_unbounded_or_zero_work_before_writing(tmp_path, seconds):
    result = subprocess.run(command(seconds), cwd=tmp_path, capture_output=True, timeout=5)
    assert result.returncode != 0
    assert not (tmp_path / "long-task").exists()


def test_workload_configuration_preserves_admission_and_uses_exact_script(tmp_path, monkeypatch):
    runtime, _, _ = _blocking_runtime_at(tmp_path / "project", monkeypatch)
    task = runtime.task_store.get("TASK-BLOCKING")
    identity = deepcopy(task.contract.evidence_contract)
    task.contract.validation = {"commands": []}
    events_before = [e.id for e in runtime.event_log.read_all()]
    workload = NativeLongTask()
    workload.configure(runtime, task, "test-seed")
    saved = runtime.task_store.get(task.id)
    assert saved.contract.evidence_contract == identity
    assert [e.id for e in runtime.event_log.read_all()] == events_before
    assert saved.contract.scope == ["README.md", "long-task/**"]
    assert saved.contract.validation["commands"][0]["command"] == saved.contract.verification
    assert " verify --seed test-seed --seconds 60" in saved.contract.verification
    assert "verification-evidence.txt" in saved.contract.acceptance_criteria[-1]
    assert workload.script.read_bytes() == SCRIPT.read_bytes()
    assert not (runtime.project_root / "long-task").exists()
    assert all("long-task.py" in role.allowed_tools[-1] for role in runtime.config.roles)
