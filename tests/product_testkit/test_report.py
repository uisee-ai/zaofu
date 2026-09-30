from __future__ import annotations

import hashlib
from pathlib import Path

from .executor import ProductRun
from .manifest import ProductManifest
from .report import build_verification_result, write_junit_report, write_verification_result
from tests.testkit.command_runner import CommandReceipt


def _manifest() -> ProductManifest:
    return ProductManifest({
        "schema_version": "product-test-manifest.v1", "project_type": "library",
        "source_root": ".", "target_commit": "a" * 40, "setup": [],
        "commands": [{"id": "unit", "command": "true", "tier": "unit",
                       "acceptance_ids": ["AC-1"], "mandatory": True,
                       "deterministic": True, "timeout_seconds": 10,
                       "domain_ids": ["P-01", "P-04"]}],
        "acceptances": [{"id": "AC-1"}], "entrypoints": [],
        "capabilities": {}, "budgets": {}, "not_applicable": {}, "cleanup": [],
    })


def _receipt(ok: bool = True, *, timed_out: bool = False) -> CommandReceipt:
    return CommandReceipt("unit", "true", "digest", ".", 0 if ok else 2,
                          timed_out, 0.1, "unit.stdout", "unit.stderr", False, "")


def test_report_passes_only_when_every_declared_command_has_receipt(tmp_path: Path):
    manifest = _manifest()
    result = build_verification_result(manifest, ProductRun((_receipt(),)), target_commit="a" * 40, run_id="run-1")
    assert result["verdict"] == "pass"
    assert result["domains"] == []
    assert result["domain_coverage"] == {"selected": [], "executed": [], "missing": []}
    assert result["ci_plan"]["T0"] == ["unit"]
    assert result["commands"][0]["command_digest"] == "digest"
    assert result["command_plan"] == [{
        "command_id": "unit",
        "command_digest": hashlib.sha256(b"true").hexdigest(),
    }]
    path = tmp_path / "verification-result.json"
    write_verification_result(result, path)
    assert '"schema_version": "verification-result.v1"' in path.read_text()


def test_report_records_executed_and_missing_domain_coverage():
    manifest = _manifest()
    run = ProductRun((_receipt(),), domains=(
        {"capability": "core", "domains": ["P-01", "P-05"], "status": "selected"},
    ))
    result = build_verification_result(manifest, run, target_commit="a" * 40, run_id="run-1")
    assert result["domain_coverage"] == {
        "selected": ["P-01", "P-05"], "executed": ["P-01"], "missing": ["P-05"]
    }


def test_report_marks_missing_or_failed_commands_not_pass():
    manifest = _manifest()
    missing = build_verification_result(manifest, ProductRun(()), target_commit="a" * 40, run_id="run-1")
    assert missing["verdict"] == "not_run"
    failed = build_verification_result(manifest, ProductRun((_receipt(False),), "unit"), target_commit="a" * 40, run_id="run-1")
    assert failed["verdict"] == "fail"


def test_optional_command_failure_does_not_fail_required_verdict():
    manifest = _manifest()
    manifest.raw["commands"].append({
        "id": "optional", "command": "false", "tier": "unit", "acceptance_ids": ["AC-1"],
        "domain_ids": ["P-04"], "mandatory": False, "deterministic": True, "timeout_seconds": 10,
    })
    optional_receipt = CommandReceipt("optional", "false", "digest", ".", 1, False, 0.1, "o.out", "o.err", False, "")
    result = build_verification_result(
        manifest, ProductRun((_receipt(), optional_receipt)), target_commit="a" * 40, run_id="run-1"
    )
    assert result["verdict"] == "pass"


def test_report_rejects_target_commit_drift():
    result = build_verification_result(_manifest(), ProductRun((_receipt(),)), target_commit="b" * 40, run_id="run-1")
    assert result["verdict"] == "harness_failure"


def test_report_keeps_revision_drift_as_harness_failure_when_command_also_fails():
    result = build_verification_result(
        _manifest(), ProductRun((_receipt(False),)), target_commit="b" * 40, run_id="run-1"
    )
    assert result["verdict"] == "harness_failure"


def test_report_classifies_timeout_as_environment_failure():
    result = build_verification_result(
        _manifest(), ProductRun((_receipt(False, timed_out=True),)), target_commit="a" * 40, run_id="run-1"
    )
    assert result["verdict"] == "environment_failure"


def test_report_classifies_command_environment_error_as_environment_failure():
    receipt = CommandReceipt("unit", "missing", "digest", ".", None,
                             False, 0.1, "unit.stdout", "unit.stderr", True,
                             "FileNotFoundError")
    result = build_verification_result(_manifest(), ProductRun((receipt,)),
                                       target_commit="a" * 40, run_id="run-1")
    assert result["verdict"] == "environment_failure"


def test_report_classifies_recovery_and_concurrency_environment_errors():
    manifest = _manifest()
    manifest.raw["recovery_scenarios"] = [{
        "id": "restart", "command": "python worker.py state ready",
        "state_path": "state", "ready_path": "ready", "tier": "integration",
        "acceptance_ids": ["AC-1"], "domain_ids": ["P-12"],
        "mandatory": True, "deterministic": True, "timeout_seconds": 10,
    }]
    manifest.raw["concurrency_scenarios"] = [{
        "id": "parallel", "commands": ["python worker.py a", "python worker.py b"],
        "tier": "integration", "acceptance_ids": ["AC-1"], "domain_ids": ["P-13"],
        "mandatory": True, "deterministic": True, "timeout_seconds": 10,
    }]
    run = ProductRun(
        (_receipt(),),
        recovery_scenarios=({"scenario_id": "restart", "passed": False, "verdict": "environment_failure", "error": "missing"},),
        concurrency_scenarios=({"scenario_id": "parallel", "passed": False, "verdict": "environment_failure", "error": "timeout"},),
    )
    result = build_verification_result(manifest, run, target_commit="a" * 40, run_id="run-1")
    assert result["verdict"] == "environment_failure"


def test_report_includes_persistence_scenario_and_maps_domain():
    manifest = _manifest()
    manifest.raw["persistence_scenarios"] = [{
        "id": "migration", "commands": ["python migrate.py"], "tier": "integration",
        "acceptance_ids": ["AC-1"], "domain_ids": ["P-09"], "mandatory": True,
        "deterministic": True, "timeout_seconds": 10,
    }]
    run = ProductRun(
        (_receipt(),),
        domains=({"capability": "persistence", "domains": ["P-09"], "status": "selected"},),
        persistence_scenarios=({"scenario_id": "migration", "passed": True, "verdict": "pass"},),
    )
    result = build_verification_result(manifest, run, target_commit="a" * 40, run_id="run-1")
    assert result["verdict"] == "pass"
    assert result["persistence_scenarios"][0]["scenario_id"] == "migration"
    assert result["domain_coverage"]["executed"] == ["P-09"]


def test_report_uses_openapi_semantic_verdict_and_counts_its_domain_as_executed():
    manifest = _manifest()
    manifest.raw["api_contracts"] = [{
        "id": "openapi",
        "tier": "integration",
        "domain_ids": ["P-07"],
        "mandatory": True,
        "deterministic": True,
        "acceptance_ids": ["AC-1"],
        "schema": "openapi.json",
    }]
    api_result = {
        "contract_id": "openapi",
        "verdict": "fail",
        "passed": False,
        "reason": "product_finding",
    }
    run = ProductRun(
        (_receipt(),),
        domains=({"capability": "api", "domains": ["P-07"], "status": "selected"},),
        api_contracts=(api_result,),
    )

    result = build_verification_result(manifest, run, target_commit="a" * 40, run_id="run-1")

    assert result["verdict"] == "fail"
    assert result["missing_commands"] == []
    assert result["domain_coverage"] == {
        "selected": ["P-07"], "executed": ["P-07"], "missing": []
    }
    assert result["api_contracts"] == [api_result]


def test_optional_api_finding_does_not_fail_required_verdict():
    manifest = _manifest()
    manifest.raw["api_contracts"] = [{"id": "optional-api", "tier": "integration", "domain_ids": ["P-07"], "mandatory": False}]
    run = ProductRun((_receipt(),), api_contracts=({
        "contract_id": "optional-api", "verdict": "fail", "passed": False,
        "mandatory": False, "reason": "product_finding",
    },))
    result = build_verification_result(manifest, run, target_commit="a" * 40, run_id="run-1")
    assert result["verdict"] == "pass"


def test_junit_report_preserves_failure_and_timeout(tmp_path: Path):
    result = {"commands": [
        {"command_id": "ok", "passed": True, "timed_out": False},
        {"command_id": "bad", "passed": False, "timed_out": False, "exit_code": 2},
        {"command_id": "slow", "passed": False, "timed_out": True, "exit_code": None},
    ]}
    path = tmp_path / "junit.xml"
    write_junit_report(result, path)
    xml = path.read_text()
    assert 'tests="3"' in xml and 'failures="1"' in xml and 'errors="1"' in xml
    assert 'name="bad"' in xml and 'type="timeout"' in xml


def test_junit_report_preserves_openapi_product_finding(tmp_path: Path):
    result = {
        "commands": [],
        "api_contracts": [{
            "contract_id": "openapi",
            "verdict": "fail",
            "reason": "product_finding",
        }],
    }
    path = tmp_path / "junit.xml"

    write_junit_report(result, path)

    xml = path.read_text(encoding="utf-8")
    assert 'name="api.openapi"' in xml
    assert 'failures="1"' in xml
    assert 'type="product-test"' in xml


def test_report_preserves_browser_product_finding_and_domain():
    manifest = _manifest()
    manifest.raw["browser_tests"] = [{"id": "browser", "tier": "e2e", "domain_ids": ["P-08"]}]
    run = ProductRun(
        (_receipt(),),
        domains=({"capability": "web", "domains": ["P-08"], "status": "selected"},),
        browser_tests=({"test_id": "browser", "verdict": "fail", "reason": "product_finding"},),
    )
    result = build_verification_result(manifest, run, target_commit="a" * 40, run_id="run-1")
    assert result["verdict"] == "fail"
    assert result["domain_coverage"]["executed"] == ["P-08"]
    assert result["missing_commands"] == []
