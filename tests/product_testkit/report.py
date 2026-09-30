"""Machine-readable aggregation for generated-product test execution."""

from __future__ import annotations

from datetime import datetime, timezone
from dataclasses import asdict
import json
from pathlib import Path
import shlex
from typing import Any, Mapping
from xml.etree.ElementTree import Element, SubElement, tostring

from .executor import ProductRun
from .manifest import ProductManifest
from .verdict import ProductVerdict
from .tiers import ci_plan
from tests.testkit.evidence import sha256_text


def build_verification_result(
    manifest: ProductManifest,
    run: ProductRun,
    *,
    target_commit: str,
    run_id: str,
    candidate_ref: str | None = None,
) -> dict[str, Any]:
    """Build a fail-closed summary; it never upgrades missing evidence to pass."""

    commands = {
        str(item["id"]): item
        for item in (*manifest.raw.get("setup", []), *manifest.test_units)
    }
    rows: list[dict[str, Any]] = []
    for receipt in run.receipts:
        declaration = commands.get(receipt.command_id)
        rows.append({
            "command_id": receipt.command_id,
            "command_digest": receipt.command_digest,
            "mandatory": bool(declaration and declaration.get("mandatory", True)),
            "passed": receipt.passed,
            "exit_code": receipt.exit_code,
            "timed_out": receipt.timed_out,
            "environment_error": receipt.environment_error,
            "error": receipt.error,
            "stdout_ref": receipt.stdout_ref,
            "stderr_ref": receipt.stderr_ref,
        })
    all_declared = [*manifest.raw.get("setup", []), *manifest.test_units]
    planned_ids = (
        set(run.planned_command_ids)
        if run.planned_command_ids is not None
        else {str(item["id"]) for item in all_declared}
    )
    all_declared = [item for item in all_declared if str(item["id"]) in planned_ids]
    command_plan = [
        {
            "command_id": str(item["id"]),
            "command_digest": _plan_digest(item),
        }
        for item in all_declared
    ]
    api_contracts = [dict(row) for row in run.api_contracts]
    browser_tests = [dict(row) for row in run.browser_tests]
    performance_tests = [dict(row) for row in run.performance_tests]
    release_artifacts = [dict(row) for row in run.release_artifacts]
    security_scans = [dict(row) for row in run.security_scans]
    release_scenarios = [dict(row) for row in run.release_scenarios]
    recovery_scenarios = [dict(row) for row in run.recovery_scenarios]
    concurrency_scenarios = [dict(row) for row in run.concurrency_scenarios]
    persistence_scenarios = [dict(row) for row in run.persistence_scenarios]
    executed_test_ids = {r["command_id"] for r in rows} | {
        str(row.get("contract_id")) for row in api_contracts
    } | {str(row.get("test_id")) for row in browser_tests} | {
        str(row.get("test_id")) for row in performance_tests
    } | {str(row.get("artifact_id")) for row in release_artifacts}
    executed_test_ids |= {str(row.get("scan_id")) for row in security_scans}
    executed_test_ids |= {str(row.get("scenario_id")) for row in release_scenarios}
    executed_test_ids |= {str(row.get("scenario_id")) for row in recovery_scenarios}
    executed_test_ids |= {str(row.get("scenario_id")) for row in concurrency_scenarios}
    executed_test_ids |= {str(row.get("scenario_id")) for row in persistence_scenarios}
    missing = [str(item["id"]) for item in all_declared if item["id"] not in executed_test_ids]
    services = []
    for receipt in run.services:
        row = asdict(receipt)
        row["passed"] = receipt.passed
        services.append(row)
    containers = []
    for receipt in run.containers:
        row = asdict(receipt)
        row["passed"] = receipt.passed
        containers.append(row)
    mock_services = []
    for receipt in run.mock_services:
        row = asdict(receipt)
        row["passed"] = receipt.passed
        mock_services.append(row)
    executed_ids = executed_test_ids
    all_selected_domains = {
        str(domain)
        for row in run.domains
        if row.get("status") == "selected"
        for domain in row.get("domains", [])
    }
    # ``planned_command_ids`` is populated for a tier/domain-scoped run.  In
    # that case only domains represented by the selected units belong to this
    # result; higher-tier domains are intentionally ``NOT_RUN`` in a later
    # tier, not a failure of this earlier tier.  The complete capability plan
    # remains in ``domains`` so downstream reports can audit the omission.
    declared_ids = {
        str(item["id"])
        for item in [*manifest.raw.get("setup", []), *manifest.test_units]
    }
    planned = set(run.planned_command_ids or declared_ids)
    if planned == declared_ids:
        selected_domains = all_selected_domains
    else:
        selected_domains = {
            str(domain)
            for item in all_declared
            if str(item.get("id")) in planned
            for domain in (item.get("domain_ids") or [])
            if str(domain) in all_selected_domains
        }
    executed_domains = {
        str(domain)
        for item in all_declared
        if str(item["id"]) in executed_ids
        for domain in (item.get("domain_ids") or [])
    }
    if target_commit != str(manifest.raw["target_commit"]):
        # A receipt from the wrong revision is never a product verdict; it is
        # a harness failure even when the command itself also failed.
        verdict = ProductVerdict.HARNESS_FAILURE
    elif run.harness_errors:
        verdict = ProductVerdict.HARNESS_FAILURE
    elif run.container_environment_error:
        verdict = ProductVerdict.ENVIRONMENT_FAILURE
    elif any(row.get("verdict") == ProductVerdict.HARNESS_FAILURE.value for row in api_contracts):
        verdict = ProductVerdict.HARNESS_FAILURE
    elif any(row.get("verdict") == ProductVerdict.HARNESS_FAILURE.value for row in browser_tests):
        verdict = ProductVerdict.HARNESS_FAILURE
    elif any(row.get("verdict") == ProductVerdict.HARNESS_FAILURE.value for row in performance_tests):
        verdict = ProductVerdict.HARNESS_FAILURE
    elif any(row.get("mandatory", True) and not row.get("passed") for row in release_artifacts):
        verdict = ProductVerdict.FAIL
    elif any(row.get("verdict") == ProductVerdict.HARNESS_FAILURE.value for row in security_scans):
        verdict = ProductVerdict.HARNESS_FAILURE
    elif any(row.get("verdict") == ProductVerdict.ENVIRONMENT_FAILURE.value for row in security_scans):
        verdict = ProductVerdict.ENVIRONMENT_FAILURE
    elif any(not row.get("passed") for row in containers):
        verdict = ProductVerdict.ENVIRONMENT_FAILURE
    elif any(not row.get("passed") for row in mock_services):
        verdict = ProductVerdict.HARNESS_FAILURE
    elif any(not row["passed"] for row in services):
        verdict = ProductVerdict.ENVIRONMENT_FAILURE
    elif any(row.get("verdict") == ProductVerdict.ENVIRONMENT_FAILURE.value for row in api_contracts):
        verdict = ProductVerdict.ENVIRONMENT_FAILURE
    elif any(row.get("verdict") == ProductVerdict.ENVIRONMENT_FAILURE.value for row in browser_tests):
        verdict = ProductVerdict.ENVIRONMENT_FAILURE
    elif any(row.get("verdict") == ProductVerdict.ENVIRONMENT_FAILURE.value for row in performance_tests):
        verdict = ProductVerdict.ENVIRONMENT_FAILURE
    elif any(row.get("verdict") == ProductVerdict.ENVIRONMENT_FAILURE.value for row in recovery_scenarios):
        verdict = ProductVerdict.ENVIRONMENT_FAILURE
    elif any(row.get("verdict") == ProductVerdict.ENVIRONMENT_FAILURE.value for row in concurrency_scenarios):
        verdict = ProductVerdict.ENVIRONMENT_FAILURE
    elif any(row.get("verdict") == ProductVerdict.ENVIRONMENT_FAILURE.value for row in release_scenarios):
        verdict = ProductVerdict.ENVIRONMENT_FAILURE
    elif any(row.get("verdict") == ProductVerdict.ENVIRONMENT_FAILURE.value for row in persistence_scenarios):
        verdict = ProductVerdict.ENVIRONMENT_FAILURE
    elif not rows and not api_contracts and not browser_tests and not performance_tests and not release_artifacts and not security_scans and not release_scenarios and not recovery_scenarios and not concurrency_scenarios and not persistence_scenarios:
        verdict = ProductVerdict.NOT_RUN
    elif any(bool(row.get("environment_error")) for row in rows):
        verdict = ProductVerdict.ENVIRONMENT_FAILURE
    elif any(bool(row.get("timed_out")) for row in rows):
        verdict = ProductVerdict.ENVIRONMENT_FAILURE
    elif any(row.get("mandatory", True) and not row["passed"] for row in rows):
        verdict = ProductVerdict.FAIL
    elif any(row.get("mandatory", True) and row.get("verdict") == ProductVerdict.FAIL.value for row in api_contracts):
        verdict = ProductVerdict.FAIL
    elif any(row.get("mandatory", True) and row.get("verdict") == ProductVerdict.FAIL.value for row in browser_tests):
        verdict = ProductVerdict.FAIL
    elif any(row.get("mandatory", True) and row.get("verdict") == ProductVerdict.FAIL.value for row in performance_tests):
        verdict = ProductVerdict.FAIL
    elif any(row.get("verdict") == ProductVerdict.FAIL.value for row in recovery_scenarios):
        verdict = ProductVerdict.FAIL
    elif any(row.get("verdict") == ProductVerdict.FAIL.value for row in concurrency_scenarios):
        verdict = ProductVerdict.FAIL
    elif any(row.get("verdict") == ProductVerdict.FAIL.value for row in persistence_scenarios):
        verdict = ProductVerdict.FAIL
    elif any(row.get("mandatory", True) and row.get("verdict") == ProductVerdict.FAIL.value for row in security_scans):
        verdict = ProductVerdict.FAIL
    elif any(row.get("mandatory", True) and not row.get("passed") for row in release_scenarios):
        verdict = ProductVerdict.FAIL
    elif any(row.get("mandatory", True) and not row.get("passed") for row in recovery_scenarios):
        verdict = ProductVerdict.FAIL
    elif any(row.get("mandatory", True) and not row.get("passed") for row in concurrency_scenarios):
        verdict = ProductVerdict.FAIL
    elif any(row.get("mandatory", True) and not row.get("passed") for row in persistence_scenarios):
        verdict = ProductVerdict.FAIL
    elif any(row.get("mandatory", True) and row.get("verdict") == ProductVerdict.NOT_RUN.value for row in api_contracts):
        verdict = ProductVerdict.NOT_RUN
    elif any(row.get("mandatory", True) and row.get("verdict") == ProductVerdict.NOT_RUN.value for row in browser_tests):
        verdict = ProductVerdict.NOT_RUN
    elif any(row.get("mandatory", True) and row.get("verdict") == ProductVerdict.NOT_RUN.value for row in performance_tests):
        verdict = ProductVerdict.NOT_RUN
    elif any(row.get("mandatory", True) and row.get("verdict") == ProductVerdict.NOT_RUN.value for row in security_scans):
        verdict = ProductVerdict.NOT_RUN
    elif any(row.get("verdict") == ProductVerdict.NOT_RUN.value for row in persistence_scenarios):
        verdict = ProductVerdict.NOT_RUN
    elif missing:
        verdict = ProductVerdict.NOT_RUN
    else:
        verdict = ProductVerdict.PASS
    result = {
        "schema_version": "verification-result.v1",
        "run_id": run_id,
        "candidate_ref": candidate_ref,
        "target_commit": target_commit,
        "manifest_path": str(manifest.path) if manifest.path else None,
        "verdict": verdict.value,
        "commands": rows,
        "command_plan": command_plan,
        "api_contracts": api_contracts,
        "browser_tests": browser_tests,
        "services": services,
        "containers": containers,
        "mock_services": mock_services,
        "performance_tests": performance_tests,
        "release_artifacts": release_artifacts,
        "security_scans": security_scans,
        "release_scenarios": release_scenarios,
        "recovery_scenarios": recovery_scenarios,
        "concurrency_scenarios": concurrency_scenarios,
        "persistence_scenarios": persistence_scenarios,
        "domains": [dict(row) for row in run.domains],
        "domain_coverage": {
            "selected": sorted(selected_domains),
            "executed": sorted(selected_domains & executed_domains),
            "missing": sorted(selected_domains - executed_domains),
        },
        "ci_plan": {key: list(value) for key, value in ci_plan(manifest).items()},
        "missing_commands": missing,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    if run.failed_entrypoint_id:
        result["failure_phase"] = "execution" if run.harness_errors else "entrypoint"
    elif run.failed_api_contract_id:
        result["failure_phase"] = "api_contract"
    elif run.failed_browser_test_id:
        result["failure_phase"] = "browser_test"
    elif run.failed_performance_test_id:
        result["failure_phase"] = "performance_test"
    elif run.failed_release_artifact_id:
        result["failure_phase"] = "release_artifact"
    elif run.failed_security_scan_id:
        result["failure_phase"] = "security_scan"
    elif run.failed_release_scenario_id:
        result["failure_phase"] = "release_scenario"
    elif run.failed_recovery_scenario_id:
        result["failure_phase"] = "recovery_scenario"
    elif run.failed_concurrency_scenario_id:
        result["failure_phase"] = "concurrency_scenario"
    elif run.failed_persistence_scenario_id:
        result["failure_phase"] = "persistence_scenario"
    elif run.failed_container_id:
        result["failure_phase"] = "container"
    elif run.failed_mock_service_id:
        result["failure_phase"] = "mock_service"
    if run.harness_errors:
        result["errors"] = [dict(row) for row in run.harness_errors]
    elif run.container_error_message:
        result["errors"] = [{
            "type": "ContainerError",
            "message": run.container_error_message,
            "environment_error": run.container_environment_error,
        }]
    return result


def _plan_digest(item: Mapping[str, Any]) -> str:
    if "command" in item:
        return sha256_text("\0".join(shlex.split(str(item["command"]))))
    return sha256_text(json.dumps(dict(item), ensure_ascii=False, sort_keys=True, separators=(",", ":")))


def build_harness_failure_result(
    *,
    run_id: str,
    manifest_path: Path,
    failure_phase: str,
    error: BaseException,
    candidate_ref: str | None = None,
    target_commit: str | None = None,
) -> dict[str, Any]:
    """Build a terminal result when execution cannot reach product assertions."""

    return {
        "schema_version": "verification-result.v1",
        "run_id": run_id,
        "candidate_ref": candidate_ref,
        "target_commit": target_commit,
        "manifest_path": str(manifest_path.resolve()),
        "verdict": ProductVerdict.HARNESS_FAILURE.value,
        "failure_phase": failure_phase,
        "errors": [{"type": type(error).__name__, "message": str(error)}],
        "commands": [],
        "command_plan": [],
        "api_contracts": [],
        "services": [],
        "containers": [],
        "mock_services": [],
        "performance_tests": [],
        "release_artifacts": [],
        "security_scans": [],
        "release_scenarios": [],
        "recovery_scenarios": [],
        "concurrency_scenarios": [],
        "persistence_scenarios": [],
        "cleanup": [],
        "domains": [],
        "domain_coverage": {"selected": [], "executed": [], "missing": []},
        "ci_plan": {},
        "missing_commands": [],
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def write_verification_result(result: Mapping[str, Any], path: Path) -> None:
    """Write a UTF-8 JSON report atomically enough for a single-run receipt."""

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(dict(result), ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    temporary.replace(path)


def write_junit_report(result: Mapping[str, Any], path: Path) -> None:
    """Write a small JUnit document while preserving non-product failures."""

    service_cases = [
        {
            **dict(row),
            "command_id": f"entrypoint.{row.get('entrypoint_id', 'unknown')}",
            "environment_error": not bool(row.get("passed")),
        }
        for row in (result.get("services") or [])
    ]
    cleanup_cases = [
        {**dict(row), "cleanup_error": not bool(row.get("passed"))}
        for row in (result.get("cleanup") or [])
    ]
    api_cases = []
    for row in result.get("api_contracts") or []:
        verdict = row.get("verdict")
        api_cases.append({
            **dict(row),
            "command_id": f"api.{row.get('contract_id', 'unknown')}",
            "passed": verdict == ProductVerdict.PASS.value,
            "environment_error": verdict == ProductVerdict.ENVIRONMENT_FAILURE.value,
            "harness_error": verdict == ProductVerdict.HARNESS_FAILURE.value,
            "not_run": verdict == ProductVerdict.NOT_RUN.value,
            "message": str(row.get("reason") or ""),
        })
    browser_cases = []
    for row in result.get("browser_tests") or []:
        verdict = row.get("verdict")
        browser_cases.append({
            **dict(row),
            "command_id": f"browser.{row.get('test_id', 'unknown')}",
            "passed": verdict == ProductVerdict.PASS.value,
            "environment_error": verdict == ProductVerdict.ENVIRONMENT_FAILURE.value,
            "harness_error": verdict == ProductVerdict.HARNESS_FAILURE.value,
            "not_run": verdict == ProductVerdict.NOT_RUN.value,
            "message": str(row.get("reason") or ""),
        })
    container_cases = []
    for row in result.get("containers") or []:
        container_cases.append({
            **dict(row),
            "command_id": f"container.{row.get('name', 'unknown')}",
            "environment_error": not bool(row.get("passed")),
            "message": str(row.get("error") or ""),
        })
    mock_service_cases = []
    for row in result.get("mock_services") or []:
        mock_service_cases.append({
            **dict(row),
            "command_id": f"mock.{row.get('service_id', 'unknown')}",
            "harness_error": not bool(row.get("passed")),
            "message": str(row.get("error") or ""),
        })
    performance_cases = []
    for row in result.get("performance_tests") or []:
        verdict = row.get("verdict")
        performance_cases.append({
            **dict(row),
            "command_id": f"k6.{row.get('test_id', 'unknown')}",
            "passed": verdict == ProductVerdict.PASS.value,
            "environment_error": verdict == ProductVerdict.ENVIRONMENT_FAILURE.value,
            "harness_error": verdict == ProductVerdict.HARNESS_FAILURE.value,
            "not_run": verdict == ProductVerdict.NOT_RUN.value,
            "message": str(row.get("reason") or ""),
        })
    release_cases = []
    for row in result.get("release_artifacts") or []:
        release_cases.append({
            **dict(row),
            "command_id": f"release.{row.get('artifact_id', 'unknown')}",
            "passed": bool(row.get("passed")),
            "message": str(row.get("error") or ""),
        })
    security_cases = []
    for row in result.get("security_scans") or []:
        verdict = row.get("verdict")
        security_cases.append({
            **dict(row),
            "command_id": f"security.{row.get('scan_id', 'unknown')}",
            "passed": verdict == ProductVerdict.PASS.value,
            "environment_error": verdict == ProductVerdict.ENVIRONMENT_FAILURE.value,
            "harness_error": verdict == ProductVerdict.HARNESS_FAILURE.value,
            "not_run": verdict == ProductVerdict.NOT_RUN.value,
            "message": str(row.get("reason") or ""),
        })
    release_scenario_cases = []
    for row in result.get("release_scenarios") or []:
        verdict = row.get("verdict")
        release_scenario_cases.append({
            **dict(row),
            "command_id": f"release-scenario.{row.get('scenario_id', 'unknown')}",
            "passed": verdict == ProductVerdict.PASS.value if verdict else bool(row.get("passed")),
            "environment_error": verdict == ProductVerdict.ENVIRONMENT_FAILURE.value,
            "message": str(row.get("error") or ""),
        })
    recovery_cases = [
        {
            **dict(row),
            "command_id": f"recovery.{row.get('scenario_id', 'unknown')}",
            "passed": row.get("verdict") == ProductVerdict.PASS.value if row.get("verdict") else bool(row.get("passed")),
            "environment_error": row.get("verdict") == ProductVerdict.ENVIRONMENT_FAILURE.value,
            "message": str(row.get("error") or ""),
        }
        for row in result.get("recovery_scenarios") or []
    ]
    concurrency_cases = [
        {
            **dict(row),
            "command_id": f"concurrency.{row.get('scenario_id', 'unknown')}",
            "passed": row.get("verdict") == ProductVerdict.PASS.value if row.get("verdict") else bool(row.get("passed")),
            "environment_error": row.get("verdict") == ProductVerdict.ENVIRONMENT_FAILURE.value,
            "message": str(row.get("error") or ""),
        }
        for row in result.get("concurrency_scenarios") or []
    ]
    persistence_cases = [
        {
            **dict(row),
            "command_id": f"persistence.{row.get('scenario_id', 'unknown')}",
            "passed": row.get("verdict") == ProductVerdict.PASS.value if row.get("verdict") else bool(row.get("passed")),
            "environment_error": row.get("verdict") == ProductVerdict.ENVIRONMENT_FAILURE.value,
            "message": str(row.get("error") or ""),
        }
        for row in result.get("persistence_scenarios") or []
    ]
    cases = (
        list(result.get("commands") or []) + api_cases + browser_cases
        + service_cases + container_cases + mock_service_cases + performance_cases + release_cases + security_cases + release_scenario_cases + recovery_cases + concurrency_cases + persistence_cases + cleanup_cases
    )
    errors = list(result.get("errors") or [])
    if errors:
        cases = [{
            "command_id": f"testkit.{result.get('failure_phase') or 'closure'}",
            "passed": False,
            "harness_error": True,
            "message": str(errors[0].get("message", "TestKit did not complete")),
        }, *cases]
    elif result.get("verdict") == ProductVerdict.FLAKY.value:
        cases = [{
            "command_id": "testkit.stability",
            "passed": False,
            "flaky": True,
        }, *cases]
    elif not cases and result.get("verdict") == ProductVerdict.NOT_RUN.value:
        cases = [{
            "command_id": "testkit.selection",
            "passed": False,
            "not_run": True,
        }]
    elif not cases and result.get("verdict") != ProductVerdict.PASS.value:
        cases = [{
            "command_id": f"testkit.{result.get('failure_phase') or 'closure'}",
            "passed": False,
            "harness_error": True,
            "message": "TestKit did not complete",
        }]
    suite_attributes = {
        "name": "zaoFu-product",
        "tests": str(len(cases)),
        "failures": str(
            sum(
                1 for row in cases
                if not row.get("passed")
                and not row.get("timed_out")
                and not row.get("environment_error")
                and not row.get("harness_error")
                and not row.get("cleanup_error")
                and not row.get("flaky")
                and not row.get("not_run")
            )
            + sum(1 for row in cases if row.get("flaky"))
        ),
        "errors": str(sum(
            1 for row in cases
            if row.get("timed_out") or row.get("environment_error") or row.get("harness_error")
            or row.get("cleanup_error")
        )),
    }
    skipped_count = sum(1 for row in cases if row.get("not_run"))
    if skipped_count:
        suite_attributes["skipped"] = str(skipped_count)
    suite = Element("testsuite", suite_attributes)
    for row in cases:
        case = SubElement(suite, "testcase", {"name": str(row.get("command_id", "unknown"))})
        if row.get("harness_error"):
            SubElement(case, "error", {"type": "harness-failure"}).text = str(row.get("message") or "")
        elif row.get("flaky"):
            SubElement(case, "failure", {"type": "flaky"}).text = "attempt outcomes changed"
        elif row.get("cleanup_error"):
            SubElement(case, "error", {"type": "cleanup-failure"}).text = (
                str(row.get("error") or "") or f"exit_code={row.get('exit_code')}"
            )
        elif row.get("not_run"):
            SubElement(case, "skipped", {"type": "not-run"}).text = "no commands selected for requested tier"
        elif row.get("environment_error"):
            SubElement(case, "error", {"type": "environment-failure"}).text = str(row.get("error") or "")
        elif row.get("timed_out"):
            SubElement(case, "error", {"type": "timeout"}).text = "command timed out"
        elif not row.get("passed"):
            SubElement(case, "failure", {"type": "product-test"}).text = (
                f"exit_code={row.get('exit_code')}"
            )
    xml = tostring(suite, encoding="unicode")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(xml + "\n", encoding="utf-8")
    temporary.replace(path)

