"""Ordinary evidence is explicit, bounded and survives its resource owner."""

import base64
import hashlib
import json
import os
from pathlib import Path

import pytest

from tests.testkit.case_evidence import CaseEvidence, CaptureBudget


def metadata():
    return {
        "given": "private synthetic binary state", "when": "raise before cleanup",
        "then": "original bytes preserved", "forbidden": ["read outside owned root"],
        "identity": {"run_id": None, "task_id": None, "attempt": None, "generation": None,
                     "operation_id": None, "reason": "helper probe has no runtime identities"},
    }


def journal(tmp_path, **limits):
    owned = tmp_path / "owned"
    owned.mkdir(exist_ok=True)
    root = owned / "state"
    root.mkdir(exist_ok=True)
    evidence = CaseEvidence(
        nodeid="test_binary", source=Path(__file__), owned_root=owned,
        output=tmp_path / "output", budget=CaptureBudget(), **limits,
    )
    evidence.register(root, metadata=metadata(), synthetic_no_secrets=True)
    return evidence, root


def test_raw_bytes_and_metadata_survive_deleted_source(tmp_path):
    evidence, root = journal(tmp_path)
    raw = b'broken-json\xff\x00{"value":'
    (root / "state.bin").write_bytes(raw)
    evidence.checkpoint("call-report")
    (root / "state.bin").unlink()
    root.rmdir()
    record = evidence.record()
    assert not record["errors"]
    snapshot = record["snapshots"]["call-report"]
    path = Path(snapshot["path"])
    assert hashlib.sha256(path.read_bytes()).hexdigest() == snapshot["digest"]
    payload = json.loads(path.read_text())
    assert payload["complete_for_declared_scope"]
    assert payload["capture_point"] == "call-report"
    assert payload["metadata"] == metadata()
    assert payload["source_digest"] == hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    [entry] = payload["files"]
    assert base64.b64decode(entry["base64"], validate=True) == raw
    assert entry["digest"] == hashlib.sha256(raw).hexdigest()
    assert entry["path"] == "state.bin" and entry["bytes"] == len(raw)


@pytest.mark.parametrize("problem", ["symlink", "ancestor_link", "fifo", "hardlink", "bytes", "files", "entries", "missing"])
def test_unsafe_or_incomplete_scope_is_not_success(tmp_path, problem):
    limits = {"max_bytes": 1} if problem == "bytes" else {}
    if problem == "files":
        limits["max_files"] = 0
    if problem == "entries":
        limits["max_entries"] = 0
    evidence, root = journal(tmp_path, **limits)
    if problem == "symlink":
        (root / "escape").symlink_to(Path(__file__))
    elif problem == "ancestor_link":
        root.rename(root.with_name("moved"))
        root.symlink_to(root.with_name("moved"), target_is_directory=True)
    elif problem == "fifo":
        os.mkfifo(root / "pipe")
    elif problem == "hardlink":
        outside = tmp_path / "outside"
        outside.write_bytes(b"not authorized")
        os.link(outside, root / "link")
    elif problem == "missing":
        root.rmdir()
    else:
        (root / "data").write_bytes(b"123")
    evidence.checkpoint("call-report")
    record = evidence.record()
    assert record["errors"]
    payload = json.loads(Path(record["snapshots"]["call-report"]["path"]).read_text())
    assert not payload["complete_for_declared_scope"] and payload["capture_error"]


@pytest.mark.parametrize("problem", ["outside", "output_overlap", "secret_claim", "metadata", "repeat"])
def test_registration_does_not_silently_expand_authority(tmp_path, problem):
    owned = tmp_path / "owned"
    owned.mkdir()
    root = owned / "state"
    root.mkdir()
    output = root / "output" if problem == "output_overlap" else tmp_path / "output"
    evidence = CaseEvidence(nodeid="probe", source=Path(__file__), owned_root=owned,
                            output=output, budget=CaptureBudget())
    if problem == "outside":
        root = tmp_path
    values = metadata()
    if problem == "metadata":
        values["given"] = "x" * 20000
    if problem == "repeat":
        evidence.register(root, metadata=values, synthetic_no_secrets=True)
    with pytest.raises(ValueError):
        evidence.register(root, metadata=values, synthetic_no_secrets=problem != "secret_claim")


def test_owner_guard_keeps_pytest_baseexception_and_records_before_delete(tmp_path):
    evidence, root = journal(tmp_path)
    target = root / "state"
    target.write_bytes(b"owner failure")
    try:
        with pytest.raises(pytest.fail.Exception, match="original control exception"):
            with evidence.guard():
                pytest.fail("original control exception")
    finally:
        target.unlink()
    payload = json.loads(Path(evidence.record()["snapshots"]["owner-guard"]["path"]).read_text())
    assert base64.b64decode(payload["files"][0]["base64"]) == b"owner failure"


def test_write_error_is_separate_from_original_failure(tmp_path, monkeypatch):
    evidence, _ = journal(tmp_path)

    def deny(*args, **kwargs):
        raise OSError("owned evidence disk failure")

    monkeypatch.setattr("tests.testkit.case_evidence.write_json_atomic", deny)
    original = AssertionError("product assertion unchanged")
    with pytest.raises(AssertionError) as caught:
        with evidence.guard():
            raise original
    assert caught.value is original
    assert "owned evidence disk failure" in str(evidence.record()["errors"])


def test_run_and_case_capture_limits_are_sticky_and_slots_bounded(tmp_path):
    evidence, root = journal(tmp_path, max_captures=4)
    (root / "data").write_bytes(b"ab")
    for _ in range(20):
        evidence.checkpoint("checkpoint")
    assert len(list((tmp_path / "output").rglob("*.json"))) <= 2
    assert "capture budget" in str(evidence.record()["errors"])
    assert len(evidence.record()["errors"]) <= 8
    other = CaseEvidence(nodeid="other", source=Path(__file__), owned_root=tmp_path / "owned",
                         output=tmp_path / "second", budget=CaptureBudget(max_bytes=1))
    other.register(root, metadata=metadata(), synthetic_no_secrets=True)
    assert "run byte budget" in str(other.record()["errors"])


def test_parent_rechecks_embedded_bytes_even_if_outer_digest_is_updated(tmp_path):
    from tests.testkit.case_evidence import validate_case_evidence
    from tests.testkit.evidence import write_json_atomic

    evidence, root = journal(tmp_path)
    (root / "raw").write_bytes(b"original")
    evidence.checkpoint()
    record = evidence.record()
    entry = record["snapshots"]["checkpoint"]
    path = Path(entry["path"])
    payload = json.loads(path.read_text())
    payload["files"][0]["base64"] = base64.b64encode(b"changed!").decode()
    entry["digest"] = write_json_atomic(path, payload)
    # Validator intentionally requires the runner's fixed output layout.
    run = tmp_path / "runner"
    run.mkdir()
    (tmp_path / "output").rename(run / "case-evidence")
    for slot in record["snapshots"].values():
        slot["path"] = str(run / "case-evidence" / Path(slot["path"]).parent.name / Path(slot["path"]).name)
    _, errors = validate_case_evidence(run, ({"nodeid": evidence.nodeid, "ordinary_evidence": record},))
    assert "raw file digest/length mismatch" in str(errors)


def test_output_symlink_does_not_create_directories_outside_declared_output(tmp_path):
    owned = tmp_path / "owned"
    root = owned / "state"
    root.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    (tmp_path / "redirect").symlink_to(outside, target_is_directory=True)
    evidence = CaseEvidence(nodeid="safe-output", source=Path(__file__), owned_root=owned,
                            output=tmp_path / "redirect" / "new-evidence", budget=CaptureBudget())
    evidence.register(root, metadata=metadata(), synthetic_no_secrets=True)
    assert evidence.record()["errors"]
    assert list(outside.iterdir()) == []


@pytest.mark.parametrize("record", [
    None, "invalid", {}, {"registered": True, "snapshots": []},
    {"registered": True, "snapshots": {}},
])
def test_parent_reports_missing_or_malformed_declared_evidence(tmp_path, record):
    from tests.testkit.case_evidence import validate_case_evidence

    paths, errors = validate_case_evidence(tmp_path, ({"nodeid": "case", "status": "passed", "ordinary_evidence": record},))
    assert paths == [] and errors


def test_verdict_does_not_crash_on_malformed_ordinary_record():
    from tests.testkit.runner import _verdict

    report = {"session_finished": True, "results": {"case": {"nodeid": "case", "status": "passed", "ordinary_evidence": "invalid"}}}
    assert _verdict(report=report, exit_code=0, timed_out=False, git_head="known") == "harness_failure"
