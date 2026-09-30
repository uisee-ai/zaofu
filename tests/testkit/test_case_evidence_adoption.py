"""The capture kit must be wired into real ordinary runtime tests."""

import base64
import json
from pathlib import Path

from tests.testkit.evidence import sha256_file
from tests.testkit.runner import run_pytest


ROOT = Path(__file__).resolve().parents[2]
S3 = "tests/conformance/test_zaofu_testkit_s3.py"
S4 = "tests/conformance/test_zaofu_testkit_s4.py"
RECOVERY_NODES = [
    "test_restart_continues_dispatch_without_repeating_side_effect",
    "test_stale_retry_result_is_ignored_and_current_attempt_is_preserved",
    "test_real_orchestrator_dispatch_crash_restarts_to_baseline",
    "test_real_orchestrator_pre_dispatch_crash_restarts_to_baseline",
]


def test_runtime_cases_register_and_crash_detectors_keep_both_trees(tmp_path):
    result = run_pytest(project_root=ROOT, output_dir=tmp_path / "out", stage="ordinary-adoption",
                        nodeids=[S3, *(f"{S4}::{name}" for name in RECOVERY_NODES)], timeout_seconds=90)
    assert result.verdict == "fail" and result.exit_code == 0
    assert len(result.cases) == 11
    assert not result.collection_errors and not result.evidence_errors and not result.ordinary_diagnostics
    for case in result.cases:
        record = case["ordinary_evidence"]
        assert record["registered"] and not record["errors"]
        if case["status"] == "passed":
            assert record["retention"] == "discarded_after_all_phases_passed"
    failing = [case for case in result.cases if case["status"] == "xfailed"]
    assert len(failing) == 2
    manifest = json.loads((Path(result.failure_bundle_path) / "manifest.json").read_text())
    for case in failing:
        assert any(issue in case["phases"]["call"]["wasxfail"] for issue in ("ZF-TEST-002", "ZF-TEST-007"))
        snapshot = case["ordinary_evidence"]["snapshots"]["call-report"]
        payload = json.loads(Path(snapshot["path"]).read_text())
        assert payload["complete_for_declared_scope"]
        assert payload["metadata"]["identity"]["run_id"] is None
        assert payload["metadata"]["identity_observation"] == "before_test_action"
        files = {row["path"]: base64.b64decode(row["base64"], validate=True) for row in payload["files"]}
        assert any(name.startswith("baseline-") and name.endswith("events.jsonl") for name in files)
        assert any(name.startswith("fault-") and name.endswith("events.jsonl") for name in files)
        crash = json.loads(files["crash-receipt.json"])
        resume = json.loads(files["resume-receipt.json"])
        assert crash["returncode"] in {71, 72} and resume["returncode"] == 0
        assert crash["args"][-1] in {"before", "crash"} and resume["args"][-1] == "resume"
        fault_root = Path(crash["args"][-2])
        assert fault_root.is_relative_to(Path(payload["original_root"]))
        assert fault_root.name.startswith("fault-") and resume["args"][-2] == str(fault_root)
        [entry] = [row for row in manifest["evidence"] if row["source"] == snapshot["path"]]
        assert sha256_file(Path(result.failure_bundle_path) / entry["ref"]) == snapshot["digest"] == entry["digest"]
