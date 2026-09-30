"""Manifest-driven dynamic security scan adapter.

The adapter executes a product-declared scanner command, then independently
parses the owned ZAP JSON report.  A zero exit code without a complete report
is never sufficient for a green result.
"""

from __future__ import annotations

from pathlib import Path
import shutil
from typing import Any, Mapping

from tests.testkit.command_runner import CommandReceipt, run_command
from tests.testkit.evidence import write_json_atomic
from tests.testkit.zap import evaluate_zap_file


def run_zap_contract(
    spec: Mapping[str, Any],
    *,
    project_root: Path,
    output_dir: Path,
    env: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Run one bounded scanner command and classify its report fail-closed."""

    scan_id = str(spec["id"])
    report_relative = Path(str(spec["report"]))
    report_source = (project_root / report_relative).resolve()
    scan_dir = output_dir / "security" / scan_id
    scan_dir.mkdir(parents=True, exist_ok=True)
    receipt: CommandReceipt = run_command(
        str(spec["command"]),
        command_id=f"security.{scan_id}",
        cwd=project_root,
        output_dir=scan_dir,
        env=env,
        timeout_seconds=float(spec["timeout_seconds"]),
    )
    result: dict[str, Any] = {
        "scan_id": scan_id,
        "tool": str(spec.get("tool", "zap")),
        "report": str(spec["report"]),
        "max_risk": int(spec["max_risk"]),
        "command_receipt": receipt.__dict__,
        "verdict": "harness_failure",
        "reason": "",
        "report_ref": "",
    }
    try:
        report_source.relative_to(project_root.resolve())
    except ValueError:
        result["reason"] = "security report escapes project root"
    else:
        if receipt.environment_error or receipt.timed_out:
            result["verdict"] = "environment_failure"
            result["reason"] = receipt.error or "scanner command did not complete"
        elif not report_source.is_file() or report_source.is_symlink():
            result["reason"] = "scanner completed without a regular report"
        else:
            destination = scan_dir / "zap-report.json"
            try:
                shutil.copyfile(report_source, destination)
                verdict = evaluate_zap_file(destination, max_risk=int(spec["max_risk"]), scanner_exit_code=receipt.exit_code)
                result.update(
                    verdict=verdict.status,
                    reason=verdict.reason,
                    violations=list(verdict.violations),
                    alerts=[alert.__dict__ for alert in verdict.alerts],
                    report_ref=destination.relative_to(output_dir).as_posix(),
                )
            except (OSError, ValueError) as exc:
                result["reason"] = f"cannot capture or parse security report: {exc}"
    write_json_atomic(scan_dir / "result.json", result)
    return result


__all__ = ["run_zap_contract"]

