"""Optional OpenAPI contract-test command integration."""

from __future__ import annotations

from dataclasses import asdict
import json
from pathlib import Path
import re
import shlex
from typing import Any, Mapping
from urllib.parse import urlsplit

from tests.testkit.command_runner import CommandReceipt, run_command

from .manifest import SCHEMATHESIS_CHECKS, SCHEMATHESIS_MODES, SCHEMATHESIS_PHASES


_NETWORK_ERROR_MARKERS = ("network", "timeout", "connection", "tls", "proxy", "dns")


def build_schemathesis_command(
    schema: Path,
    *,
    base_url: str,
    checks: tuple[str, ...] = (),
    phases: tuple[str, ...] = (),
    mode: str | None = None,
    workers: int | None = None,
    seed: int | None = None,
    max_examples: int | None = None,
    max_time_seconds: int | float | None = None,
    request_timeout_seconds: int | float | None = None,
    request_retries: int | None = None,
    report_json: Path | None = None,
    report_junit: Path | None = None,
    deterministic: bool = False,
) -> str:
    """Build a reproducible Schemathesis CLI command for a product manifest."""

    try:
        parsed_url = urlsplit(base_url)
    except ValueError as exc:
        raise ValueError("base_url is invalid") from exc
    if parsed_url.scheme not in {"http", "https"} or not parsed_url.hostname:
        raise ValueError("base_url must use http or https")
    if parsed_url.username is not None or parsed_url.password is not None:
        raise ValueError("base_url must not contain credentials")
    if parsed_url.query or parsed_url.fragment:
        raise ValueError("base_url must not contain query or fragment")
    if not schema.is_file() or schema.is_symlink():
        raise FileNotFoundError(schema)
    if any(check not in SCHEMATHESIS_CHECKS for check in checks):
        raise ValueError("unsupported Schemathesis check")
    if any(phase not in SCHEMATHESIS_PHASES for phase in phases):
        raise ValueError("unsupported Schemathesis phase")
    if mode is not None and mode not in SCHEMATHESIS_MODES:
        raise ValueError("unsupported Schemathesis mode")
    command = ["schemathesis", "run", str(schema), "--url", base_url]
    if phases:
        command.extend(["--phases", ",".join(phases)])
    if mode is not None:
        command.extend(["--mode", mode])
    if checks:
        command.extend(["--checks", ",".join(checks)])
    for option, value in (
        ("--workers", workers),
        ("--seed", seed),
        ("--max-examples", max_examples),
        ("--max-time", max_time_seconds),
        ("--request-timeout", request_timeout_seconds),
        ("--request-retries", request_retries),
    ):
        if value is not None:
            command.extend([option, str(value)])
    if deterministic:
        command.append("--generation-deterministic")
    if report_json is not None:
        command.extend(["--report-json-path", str(report_json)])
    if report_junit is not None:
        command.extend(["--report-junit-path", str(report_junit)])
    command.extend(["--no-color", "--output-truncate", "false"])
    return " ".join(shlex.quote(part) for part in command)


def classify_schemathesis_result(
    report: Mapping[str, Any],
    *,
    outer_exit_code: int | None,
    outer_timed_out: bool,
    outer_environment_error: bool,
    expected_version: str,
    expected_phases: tuple[str, ...],
) -> dict[str, object]:
    """Map Schemathesis process and JSON facts to a fail-closed product verdict."""

    if not isinstance(report, Mapping):
        raise ValueError("Schemathesis report must be a JSON object")
    operations = report.get("operations")
    selected = operations.get("selected") if isinstance(operations, Mapping) else None
    tested = operations.get("tested") if isinstance(operations, Mapping) else None
    version = report.get("schemathesis_version")
    complete = report.get("complete")
    seed = report.get("seed")
    summary: dict[str, object] = {
        "verdict": "harness_failure",
        "reason": "invalid_report",
        "complete": bool(complete),
        "tool_version": str(version or ""),
        "operations_selected": selected if isinstance(selected, int) else 0,
        "operations_tested": tested if isinstance(tested, int) else 0,
        "seed": seed if isinstance(seed, int) and not isinstance(seed, bool) else None,
    }
    if outer_environment_error or outer_timed_out:
        summary.update(verdict="environment_failure", reason="outer_execution_failed")
        return summary
    if not isinstance(version, str) or version != expected_version:
        summary["reason"] = "version_mismatch"
        return summary
    if complete is not True or not isinstance(operations, Mapping):
        summary["reason"] = "incomplete_report"
        return summary
    if not isinstance(selected, int) or isinstance(selected, bool) or not isinstance(tested, int) or isinstance(tested, bool):
        return summary
    if selected <= 0 or tested <= 0:
        summary.update(verdict="not_run", reason="no_operations_tested")
        return summary
    failures = report.get("failures")
    errors = report.get("errors")
    warnings = report.get("warnings")
    phases = report.get("phases")
    if not isinstance(failures, list) or not isinstance(errors, list) or not isinstance(warnings, Mapping):
        return summary
    if not isinstance(phases, Mapping):
        return summary
    report_exit = report.get("exit_code")
    if not isinstance(report_exit, int) or isinstance(report_exit, bool) or report_exit != outer_exit_code:
        summary["reason"] = "exit_code_mismatch"
        return summary
    if errors:
        titles = " ".join(str(item.get("title", "")) for item in errors if isinstance(item, Mapping)).lower()
        if any(marker in titles for marker in _NETWORK_ERROR_MARKERS):
            summary.update(verdict="environment_failure", reason="target_transport_error")
        else:
            summary["reason"] = "schemathesis_runtime_error"
        return summary
    if failures:
        summary.update(verdict="fail", reason="product_finding")
        return summary
    if any(value for value in warnings.values()):
        summary["reason"] = "configuration_warning"
        return summary
    for phase in expected_phases:
        phase_result = phases.get(phase)
        if not isinstance(phase_result, Mapping) or phase_result.get("status") != "success":
            summary["reason"] = f"phase_not_successful:{phase}"
            return summary
    if outer_exit_code != 0:
        summary["reason"] = "unexpected_nonzero_exit"
        return summary
    summary.update(verdict="pass", reason="checks_passed")
    return summary


def run_schemathesis_contract(
    contract: Mapping[str, Any],
    *,
    project_root: Path,
    output_dir: Path,
    env: Mapping[str, str] | None = None,
) -> dict[str, object]:
    """Execute one admitted stateless OpenAPI contract and preserve raw evidence."""

    contract_id = str(contract["id"])
    project = project_root.resolve()
    schema = (project / str(contract["schema"])).resolve()
    try:
        schema.relative_to(project)
    except ValueError as exc:
        raise ValueError("OpenAPI schema escapes product root") from exc
    if not schema.is_file() or schema.is_symlink():
        raise FileNotFoundError(schema)

    report_dir = output_dir.resolve() / "schemathesis" / contract_id
    report_dir.mkdir(parents=True, exist_ok=False)
    report_json = report_dir / "result.json"
    report_junit = report_dir / "junit.xml"

    version_receipt = run_command(
        ("schemathesis", "--version"),
        command_id=f"schemathesis.{contract_id}.version",
        cwd=project,
        output_dir=output_dir,
        env=env,
        timeout_seconds=10,
    )
    version_text = Path(version_receipt.stdout_ref).read_text(encoding="utf-8").strip()
    match = re.search(r"\bversion\s+(\d+\.\d+\.\d+)\b", version_text)
    actual_version = match.group(1) if match else ""
    expected_version = str(contract["tool_version"])
    if not version_receipt.passed:
        return _contract_result(
            contract,
            {
                "verdict": "environment_failure",
                "reason": "tool_probe_failed",
                "complete": False,
                "tool_version": actual_version,
                "operations_selected": 0,
                "operations_tested": 0,
                "seed": None,
            },
            version_receipt=version_receipt,
            command_receipt=None,
            report_json=report_json,
            report_junit=report_junit,
            report=None,
        )
    if actual_version != expected_version:
        return _contract_result(
            contract,
            {
                "verdict": "harness_failure",
                "reason": "version_mismatch",
                "complete": False,
                "tool_version": actual_version,
                "operations_selected": 0,
                "operations_tested": 0,
                "seed": None,
            },
            version_receipt=version_receipt,
            command_receipt=None,
            report_json=report_json,
            report_junit=report_junit,
            report=None,
        )

    command = build_schemathesis_command(
        schema,
        base_url=str(contract["base_url"]),
        checks=tuple(str(item) for item in contract["checks"]),
        phases=tuple(str(item) for item in contract["phases"]),
        mode=str(contract["mode"]),
        workers=int(contract["workers"]),
        seed=int(contract["seed"]),
        max_examples=int(contract["max_examples"]),
        max_time_seconds=int(contract["max_time_seconds"]),
        request_timeout_seconds=float(contract["request_timeout_seconds"]),
        request_retries=int(contract["request_retries"]),
        report_json=report_json,
        report_junit=report_junit,
        deterministic=bool(contract["deterministic"]),
    )
    command_receipt = run_command(
        command,
        command_id=f"schemathesis.{contract_id}",
        cwd=project,
        output_dir=output_dir,
        env=env,
        timeout_seconds=float(contract["timeout_seconds"]),
    )
    try:
        raw_report = json.loads(report_json.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        verdict = "environment_failure" if (
            command_receipt.timed_out or command_receipt.environment_error
        ) else "harness_failure"
        reason = "outer_execution_failed" if verdict == "environment_failure" else "missing_or_invalid_report"
        evaluation = {
            "verdict": verdict,
            "reason": reason,
            "complete": False,
            "tool_version": actual_version,
            "operations_selected": 0,
            "operations_tested": 0,
            "seed": None,
        }
        raw_report = None
    else:
        evaluation = classify_schemathesis_result(
            raw_report,
            outer_exit_code=command_receipt.exit_code,
            outer_timed_out=command_receipt.timed_out,
            outer_environment_error=command_receipt.environment_error,
            expected_version=expected_version,
            expected_phases=tuple(str(item) for item in contract["phases"]),
        )
    return _contract_result(
        contract,
        evaluation,
        version_receipt=version_receipt,
        command_receipt=command_receipt,
        report_json=report_json,
        report_junit=report_junit,
        report=raw_report,
    )


def _receipt_payload(receipt: CommandReceipt | None) -> dict[str, object] | None:
    if receipt is None:
        return None
    return {**asdict(receipt), "passed": receipt.passed}


def _contract_result(
    contract: Mapping[str, Any],
    evaluation: Mapping[str, object],
    *,
    version_receipt: CommandReceipt,
    command_receipt: CommandReceipt | None,
    report_json: Path,
    report_junit: Path,
    report: Mapping[str, Any] | None,
) -> dict[str, object]:
    return {
        "contract_id": str(contract["id"]),
        "entrypoint_id": str(contract["entrypoint_id"]),
        "expected_version": str(contract["tool_version"]),
        "phases": list(contract["phases"]),
        "mode": str(contract["mode"]),
        "checks": list(contract["checks"]),
        **dict(evaluation),
        "passed": evaluation.get("verdict") == "pass",
        "version_probe": _receipt_payload(version_receipt),
        "command_receipt": _receipt_payload(command_receipt),
        "report_json_ref": str(report_json),
        "report_junit_ref": str(report_junit),
        "failures": list(report.get("failures", [])) if report else [],
        "errors": list(report.get("errors", [])) if report else [],
        "warnings": dict(report.get("warnings", {})) if report else {},
    }

