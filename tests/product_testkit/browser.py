"""Docker Playwright execution and fail-closed browser result classification."""

from __future__ import annotations

from dataclasses import asdict
import json
import os
from pathlib import Path
import shlex
from typing import Any, Mapping
from urllib.parse import urlsplit

from tests.testkit.command_runner import CommandReceipt, run_command

from .docker import probe_docker


def build_playwright_command(
    *,
    project_root: Path,
    image: str,
    spec: str,
    base_url: str,
    install_timeout_seconds: int,
    report_path: Path,
    output_dir: Path | None = None,
    install_browser: bool = False,
    config_path: Path | None = None,
) -> list[str]:
    """Build the repository's bounded, host-network Docker browser command."""

    project = project_root.resolve()
    if not image or any(char.isspace() for char in image):
        raise ValueError("image must be one Docker image token")
    spec_path = Path(spec)
    if not spec.strip() or spec_path.is_absolute() or ".." in spec_path.parts:
        raise ValueError("spec must stay inside the product root")
    parsed = urlsplit(base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("base_url must use http or https")
    if parsed.username is not None or parsed.password is not None or parsed.query or parsed.fragment:
        raise ValueError("base_url must not contain credentials, query or fragment")
    if not isinstance(install_timeout_seconds, int) or isinstance(install_timeout_seconds, bool) or install_timeout_seconds <= 0:
        raise ValueError("install_timeout_seconds must be a positive integer")
    report = report_path.resolve()
    mounts = ["-v", f"{project}:/work", "-w", "/work"]
    if output_dir is None:
        try:
            report_ref = "/work/" + report.relative_to(project).as_posix()
        except ValueError as exc:
            raise ValueError("report_path must be inside project_root when output_dir is omitted") from exc
    else:
        evidence = output_dir.resolve()
        try:
            report_ref = "/evidence/" + report.relative_to(evidence).as_posix()
        except ValueError as exc:
            raise ValueError("report_path must be inside output_dir") from exc
        mounts.extend(["-v", f"{evidence}:/evidence"])
    config_ref = ""
    if config_path is not None:
        config = config_path.resolve()
        if output_dir is None:
            try:
                config_ref = "/work/" + config.relative_to(project).as_posix()
            except ValueError as exc:
                raise ValueError("config_path must be inside project_root") from exc
        else:
            try:
                config_ref = "/evidence/" + config.relative_to(output_dir.resolve()).as_posix()
            except ValueError as exc:
                raise ValueError("config_path must be inside output_dir") from exc
    browser_path = "0" if install_browser else "/ms-playwright"
    commands = []
    if install_browser:
        commands.append(f"timeout {install_timeout_seconds}s npx playwright install chromium")
    test_command = f"npx playwright test {shlex.quote(spec)}"
    if config_ref:
        test_command += f" --config {shlex.quote(config_ref)}"
    test_command += f" --output {shlex.quote('/evidence/test-results' if output_dir is not None else '/work/test-results')}"
    test_command += f" --reporter=json > {shlex.quote(report_ref)}"
    commands.append(test_command)
    if not install_browser:
        commands.insert(0, "export ZF_BROWSER_EXECUTABLE=\"$(find /ms-playwright -type f -path '*/chrome-linux64/chrome' | sort -V | tail -n 1)\"")
    script = "set -euo pipefail; " + "; ".join(commands)
    return [
        "docker", "run", "--rm", "--network", "host",
        "--user", f"{os.getuid()}:{os.getgid()}",
        "--entrypoint", "bash", *mounts,
        "-e", f"PLAYWRIGHT_BROWSERS_PATH={browser_path}",
        "-e", "NODE_PATH=/work/node_modules",
        "-e", f"ZF_BROWSER_BASE_URL={base_url}",
        "-e", f"ZF_BROWSER_ARTIFACT_DIR={'/evidence/test-results' if output_dir is not None else '/work/test-results'}",
        image, "-lc", script,
    ]


def classify_playwright_result(
    report: Mapping[str, Any],
    *,
    outer_exit_code: int | None,
    outer_timed_out: bool,
    outer_environment_error: bool,
) -> dict[str, object]:
    """Classify Playwright JSON without treating a green exit alone as proof."""

    if outer_timed_out or outer_environment_error:
        return {"verdict": "environment_failure", "reason": "outer_execution_failed", "tests": 0}
    if not isinstance(report, Mapping):
        return {"verdict": "harness_failure", "reason": "missing_or_invalid_report", "tests": 0}
    stats = report.get("stats")
    if not isinstance(stats, Mapping):
        return {"verdict": "harness_failure", "reason": "missing_stats", "tests": 0}
    counts: dict[str, int] = {}
    for field in ("expected", "unexpected", "skipped", "flaky"):
        value = stats.get(field, 0)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            return {"verdict": "harness_failure", "reason": f"invalid_stats:{field}", "tests": 0}
        counts[field] = value
    total = sum(counts.values())
    result = {"verdict": "harness_failure", "reason": "invalid_report", "tests": total, **counts}
    if total == 0:
        result["verdict"] = "not_run"
        result["reason"] = "no_tests_executed"
        return result
    if counts["unexpected"]:
        result["verdict"] = "fail"
        result["reason"] = "product_finding"
        return result
    if counts["flaky"]:
        result["verdict"] = "fail"
        result["reason"] = "flaky_tests"
        return result
    if counts["skipped"]:
        result["verdict"] = "not_run"
        result["reason"] = "skipped_tests"
        return result
    errors = report.get("errors")
    if not isinstance(errors, list):
        result["reason"] = "invalid_errors"
        return result
    if errors:
        result["reason"] = "playwright_runner_error"
        return result
    if outer_exit_code != 0:
        result["reason"] = "exit_code_without_product_finding"
        return result
    result["verdict"] = "pass"
    result["reason"] = "browser_assertions_passed"
    return result


def run_playwright_contract(
    contract: Mapping[str, Any],
    *,
    project_root: Path,
    output_dir: Path,
    env: Mapping[str, str] | None = None,
) -> dict[str, object]:
    """Run one admitted browser contract and retain its JSON/receipt evidence."""

    contract_id = str(contract["id"])
    output = output_dir.resolve() / "playwright" / contract_id
    output.mkdir(parents=True, exist_ok=False)
    report_path = output / "result.json"
    probe = probe_docker()
    if not probe.available:
        return {
            "test_id": contract_id,
            "entrypoint_id": str(contract["entrypoint_id"]),
            "verdict": "environment_failure",
            "reason": "docker_unavailable",
            "tests": 0,
            "docker_probe": asdict(probe),
            "report_ref": str(report_path),
            "config_ref": "",
            "artifact_dir": str(output / "test-results"),
            "command_receipt": None,
        }
    config_path = output / "playwright.config.cjs"
    config_path.write_text(
        "const { defineConfig } = require('@playwright/test');\n"
        "module.exports = defineConfig({testDir: '/work', use: {baseURL: process.env.ZF_BROWSER_BASE_URL, "
        "launchOptions: {executablePath: process.env.ZF_BROWSER_EXECUTABLE || undefined}}});\n",
        encoding="utf-8",
    )
    command = build_playwright_command(
        project_root=project_root,
        image=str(contract["image"]),
        spec=str(contract["spec"]),
        base_url=str(contract["base_url"]),
        install_timeout_seconds=int(contract["install_timeout_seconds"]),
        report_path=report_path,
        output_dir=output,
        install_browser=bool(contract.get("install_browser", False)),
        config_path=config_path,
    )
    receipt = run_command(
        command,
        command_id=f"playwright.{contract_id}",
        cwd=project_root,
        output_dir=output_dir,
        env=env,
        timeout_seconds=float(contract["timeout_seconds"]),
    )
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        report = None
    evaluation = classify_playwright_result(
        report,
        outer_exit_code=receipt.exit_code,
        outer_timed_out=receipt.timed_out,
        outer_environment_error=receipt.environment_error,
    )
    return {
        "test_id": contract_id,
        "entrypoint_id": str(contract["entrypoint_id"]),
        **evaluation,
        "passed": evaluation.get("verdict") == "pass",
        "docker_probe": asdict(probe),
        "report_ref": str(report_path),
        "config_ref": str(config_path),
        "artifact_dir": str(output / "test-results"),
        "command_receipt": {**asdict(receipt), "passed": receipt.passed},
        "failures": list((report or {}).get("errors", [])) if isinstance(report, Mapping) else [],
    }

