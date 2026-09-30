"""Interrupted evidence must remain diagnosable without forged report refs."""

import base64
import json
from pathlib import Path

import pytest

from tests.testkit.evidence import sha256_file
from tests.testkit.runner import run_pytest


ROOT = Path(__file__).resolve().parents[2]
FIXTURE = "tests/testkit/fixtures/ordinary_window_fixture.py::test_snapshot_window"


@pytest.mark.parametrize("mode", ["first-snapshot", "new-slot", "replaced-slot", "before-replace", "partial-write", "before-registration"])
def test_actual_exit_at_snapshot_report_window(tmp_path, monkeypatch, mode):
    monkeypatch.setenv("ZF_CASE_WINDOW", mode)
    result = run_pytest(project_root=ROOT, output_dir=tmp_path / "out", nodeids=[FIXTURE], timeout_seconds=30)
    assert result.verdict == "harness_failure" and result.exit_code == 42
    assert not result.session_finished and not result.timed_out
    [case] = result.cases
    assert case["status"] == "not_run" and "call" not in case["phases"]
    report = json.loads(Path(result.report_path).read_text())
    assert report["results"][case["nodeid"]] == case  # Parent must not mint child facts.
    bundle = Path(result.failure_bundle_path)
    manifest = json.loads((bundle / "manifest.json").read_text())
    if mode == "before-registration":
        assert not case["ordinary_evidence"]["registered"]
        assert not result.ordinary_diagnostics
        assert not list((tmp_path / "out" / "case-evidence").glob("*/*.json"))
    elif mode in {"before-replace", "partial-write"}:
        assert not result.ordinary_diagnostics
        snapshot = case["ordinary_evidence"]["snapshots"]["checkpoint"]
        payload = json.loads(Path(snapshot["path"]).read_text())
        assert base64.b64decode(payload["files"][0]["base64"]) == b"old checkpoint\x00"
        temporary = list((tmp_path / "out" / "case-evidence").glob("*/.*.tmp"))
        assert temporary
        if mode == "before-replace":
            # The actual atomic writer finished its JSON temp file before
            # interception of the real replace call; it is still uncommitted.
            assert json.loads(temporary[0].read_text())["capture_point"] == "checkpoint"
        else:
            assert temporary[0].read_bytes() == b'{"partial":'
        assert all(not row["source"].endswith(".tmp") for row in manifest["evidence"])
    else:
        [diagnostic] = result.ordinary_diagnostics
        assert diagnostic["classification"] == "unbound_diagnostic_not_verdict_evidence"
        assert diagnostic["nodeid"] == case["nodeid"]
        [entry] = [row for row in manifest["evidence"] if row["source"] == diagnostic["path"]]
        assert sha256_file(bundle / entry["ref"]) == entry["digest"] == diagnostic["digest"]
        payload = json.loads((bundle / entry["ref"]).read_text())
        assert payload["complete_for_declared_scope"]
        expected = b"old checkpoint\x00" if mode == "first-snapshot" else b"new committed state\xff"
        assert base64.b64decode(payload["files"][0]["base64"], validate=True) == expected
        assert diagnostic in manifest["metadata"]["ordinary_diagnostics"]
        if mode == "replaced-slot":
            previous = case["ordinary_evidence"]["snapshots"]["checkpoint"]["digest"]
            assert diagnostic["reported_digest"] == previous != diagnostic["digest"]
            assert "snapshot digest mismatch" in str(result.evidence_errors)
        else:
            assert diagnostic["reported_digest"] is None
    assert not result.failure_bundle_error
