"""Prove capture happens before Hypothesis teardown and survives shrinking."""

import base64
import hashlib
import json
from pathlib import Path

import pytest
from hypothesis.errors import UnsatisfiedAssumption

from tests.testkit.evidence import write_json_atomic
from tests.testkit.fixtures.model_failure_fixture import CounterModel
from tests.testkit.model_evidence import ModelEvidence, replay_steps
from tests.testkit.runner import run_pytest


ROOT = Path(__file__).resolve().parents[2]
FIXTURE = "tests/testkit/fixtures/model_failure_fixture.py"


@pytest.mark.parametrize("kind", ["invariant", "rule"])
def test_generated_failure_retains_state_after_cleanup_and_replays(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str,
) -> None:
    output = tmp_path / "run"
    result = run_pytest(
        project_root=ROOT, nodeids=[f"{FIXTURE}::test_generated_{kind}_failure"],
        stage="capture-contract", output_dir=output, timeout_seconds=60,
    )
    assert result.exit_code == 1 and result.verdict == "fail"
    assert not result.timed_out and result.session_finished
    assert len(result.cases) == 1 and result.cases[0]["status"] == "failed"
    report = result.cases[0]["phases"]["call"]["longrepr"]
    assert f"synthetic {kind} counter bug" in report
    assert "state.add(" in report and "trace_sha256=" in report
    candidates = sorted((output / "model-evidence").glob("*/*.json"))
    assert {path.name for path in candidates} == {"first.json", "latest.json"}
    assert len(candidates) == 2
    latest = next(path for path in candidates if path.name == "latest.json")
    captured = json.loads(latest.read_text())
    assert captured["complete"] is True
    assert "not guaranteed" in captured["retention"]
    assert not Path(captured["original_root"]).exists()
    assert captured["python_version"] and captured["hypothesis_version"] and captured["pytest_version"]
    assert captured["seed"] is None and captured["settings"]
    assert captured["source_sha256"] == hashlib.sha256((ROOT / FIXTURE).read_bytes()).hexdigest()
    canonical_trace = json.dumps(captured["steps"], sort_keys=True, separators=(",", ":"))
    assert hashlib.sha256(canonical_trace.encode()).hexdigest() == captured["trace_sha256"]
    assert captured["trace_sha256"] in report
    files = {entry["path"]: entry for entry in captured["files"]}
    assert {"events.jsonl", "kanban.json"} <= files.keys()
    for entry in files.values():
        body = base64.b64decode(entry["base64"], validate=True)
        assert len(body) == entry["bytes"]
        assert hashlib.sha256(body).hexdigest() == entry["sha256"]
    manifest = json.loads((Path(result.failure_bundle_path) / "manifest.json").read_text())
    assert len([row for row in manifest["evidence"] if "/model-evidence/" in row["source"]]) == 2

    # Rebuild a known initial fixture; never execute code or imports from JSON.
    monkeypatch.setenv("ZF_TESTKIT_MODEL_EVIDENCE", str(tmp_path / "replay-evidence"))
    for broken in (True, False):
        model = CounterModel(broken=broken, fail_in_rule=kind == "rule")
        try:
            if broken:
                with pytest.raises(AssertionError, match=f"synthetic {kind} counter bug"):
                    replay_steps(model, captured["steps"], model.consistent)
            else:
                replay_steps(model, captured["steps"], model.consistent)
        finally:
            model.teardown()


def test_passing_model_does_not_create_failure_artifacts(tmp_path: Path) -> None:
    output = tmp_path / "pass"
    result = run_pytest(
        project_root=ROOT, nodeids=[f"{FIXTURE}::test_fixed_model"],
        stage="capture-contract", output_dir=output, timeout_seconds=60,
    )
    assert result.verdict == "pass" and result.exit_code == 0
    assert not (output / "model-evidence").exists()


@pytest.mark.parametrize("problem", ["symlink", "byte_limit", "file_limit", "write_error", "output_inside_fixture", "directory_read_error"])
def test_capture_error_preserves_original_failure_and_rejects_incomplete_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, problem: str,
) -> None:
    root = tmp_path / "fixture"
    root.mkdir()
    write_json_atomic(root / "state.json", {"value": 1})
    output = tmp_path / "output"
    monkeypatch.setenv("ZF_TESTKIT_MODEL_EVIDENCE", str(root / "evidence" if problem == "output_inside_fixture" else output))
    if problem == "symlink":
        (root / "outside").symlink_to(Path(__file__))
    if problem == "write_error":
        import os
        original_replace = os.replace

        def fail_replace(source, destination):
            # Inject failure into this model's output, not the pytest reporter
            # or unrelated fixture cleanup in the same interpreter.
            if Path(destination).is_relative_to(output):
                raise OSError("injected replace failure")
            return original_replace(source, destination)
        monkeypatch.setattr("tests.testkit.evidence.os.replace", fail_replace)
    if problem == "directory_read_error":
        blocked = root / "unreadable"
        blocked.mkdir()
        from tests.testkit import model_evidence
        original_scandir = model_evidence.os.scandir

        def unreadable_scandir(path):
            if Path(path) == blocked:
                raise PermissionError("injected directory read failure")
            return original_scandir(path)
        monkeypatch.setattr(model_evidence.os, "scandir", unreadable_scandir)
    journal = ModelEvidence(
        root=root, source=Path(__file__), model_name="CaptureProbe", reference=lambda: {},
        max_bytes=1 if problem == "byte_limit" else 1024,
        max_files=0 if problem == "file_limit" else 128,
    )
    error = AssertionError("original failure remains visible")
    with pytest.raises(AssertionError) as captured:
        with journal.guard("action", {}):
            raise error
    assert captured.value is error
    assert any("evidence unavailable" in note for note in error.__notes__)
    if problem == "write_error":
        assert any("injected replace failure" in note for note in error.__notes__)
    assert not list(output.rglob("*.json"))
    assert not list(output.rglob("*.tmp"))
    assert not (root / "evidence").exists()


def test_capture_slots_are_bounded_and_isolated_by_nodeid(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "fixture"
    root.mkdir()
    output = tmp_path / "evidence"
    monkeypatch.setenv("ZF_TESTKIT_MODEL_EVIDENCE", str(output))
    for nodeid in ("node-one", "node-two"):
        monkeypatch.setenv("PYTEST_CURRENT_TEST", f"{nodeid} (call)")
        for count in range(4):
            # Shrinking creates a new instance; it must reuse the two slots.
            write_json_atomic(root / "state.json", {"value": count})
            journal = ModelEvidence(root=root, model_name="BoundProbe", source=Path(__file__), reference=lambda: count)
            with pytest.raises(AssertionError, match="synthetic"):
                with journal.guard("set", {"value": count}):
                    raise AssertionError("synthetic")
    snapshots = list(output.glob("*/*.json"))
    assert len(snapshots) == 4 and len({path.parent for path in snapshots}) == 2
    for path in snapshots:
        captured = json.loads(path.read_text())
        assert captured["nodeid"] in {"node-one", "node-two"}
        assert captured["reference"] == (0 if path.name == "first.json" else 3)
    assert not list(output.rglob("*.tmp"))


def test_discarded_example_is_not_saved_as_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    output = tmp_path / "output"
    monkeypatch.setenv("ZF_TESTKIT_MODEL_EVIDENCE", str(output))
    journal = ModelEvidence(root=tmp_path, model_name="AssumeProbe", source=Path(__file__), reference=lambda: {})
    with pytest.raises(UnsatisfiedAssumption):
        with journal.guard("discard", {}):
            raise UnsatisfiedAssumption()
    assert not output.exists()


def test_replay_refuses_unregistered_methods() -> None:
    with pytest.raises(ValueError, match="explicitly recorded"):
        replay_steps(object(), [{"action": "__class__", "arguments": {}}], lambda: None)


def test_replay_checks_initial_invariant_even_without_actions() -> None:
    def check() -> None:
        raise AssertionError("initial invariant failed")

    with pytest.raises(AssertionError, match="initial invariant failed"):
        replay_steps(object(), [], check)
