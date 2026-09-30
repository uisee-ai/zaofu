"""Docker k6 contract runner for product performance/soak tests."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import re
from typing import Any, Mapping

from tests.testkit.command_runner import run_command

from .performance import PerformanceThreshold, evaluate_k6_summary


class K6ContractError(ValueError):
    """A k6 declaration or report cannot be trusted."""


def build_k6_command(
    *, project_root: Path, output_dir: Path, script: str, image: str,
    timeout_seconds: int, env: Mapping[str, str] | None = None,
) -> list[str]:
    script_path = Path(script)
    if script_path.is_absolute() or ".." in script_path.parts or not script_path.name:
        raise K6ContractError("k6 script must be a relative product path")
    if not image or any(char.isspace() for char in image) or any(char in image for char in "\x00\n\r"):
        raise K6ContractError("k6 image must be one Docker image token")
    if not isinstance(timeout_seconds, int) or isinstance(timeout_seconds, bool) or timeout_seconds <= 0:
        raise K6ContractError("k6 timeout_seconds must be positive")
    if not (project_root / script_path).is_file():
        raise K6ContractError(f"k6 script does not exist: {script}")
    uid = os.getuid() if hasattr(os, "getuid") else 1000
    gid = os.getgid() if hasattr(os, "getgid") else 1000
    environment: list[str] = []
    for key, value in (env or {}).items():
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", str(key)):
            raise K6ContractError("k6 environment keys must be shell identifiers")
        if "\x00" in str(value):
            raise K6ContractError("k6 environment values must not contain NUL")
        environment.extend(("-e", f"{key}={value}"))
    return [
        "docker", "run", "--rm", "--network", "host", "--user", f"{uid}:{gid}",
        "-v", f"{project_root.resolve()}:/work:ro", "-v", f"{output_dir.resolve()}:/evidence",
        *environment, image, "run", "--summary-export", "/evidence/summary.json",
        f"/work/{script_path.as_posix()}",
    ]


def run_k6_contract(
    spec: Mapping[str, Any], *, project_root: Path, output_dir: Path,
    env: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    test_id = str(spec["id"])
    output = output_dir / "k6" / test_id
    output.mkdir(parents=True, exist_ok=True)
    command = build_k6_command(
        project_root=project_root, output_dir=output, script=str(spec["script"]),
        image=str(spec["image"]), timeout_seconds=int(spec["timeout_seconds"]), env=env,
    )
    probe = subprocess.run(["docker", "image", "inspect", str(spec["image"])], capture_output=True, text=True, check=False)
    if probe.returncode != 0:
        return {"test_id": test_id, "verdict": "environment_failure", "reason": "k6 image unavailable"}
    receipt = run_command(
        command, command_id=f"k6.{test_id}", cwd=project_root, output_dir=output,
        env=env, timeout_seconds=float(spec["timeout_seconds"]),
    )
    summary_path = output / "summary.json"
    if receipt.timed_out or receipt.environment_error:
        verdict = "environment_failure"
        reason = receipt.error or "k6 timed out"
        performance = None
    else:
        payload: Mapping[str, Any] | None = None
        try:
            loaded = json.loads(summary_path.read_text(encoding="utf-8"))
            if isinstance(loaded, Mapping):
                payload = loaded
        except (OSError, UnicodeError, json.JSONDecodeError):
            payload = None
        if payload is None:
            verdict, reason, performance = "harness_failure", "missing or malformed k6 summary", None
        else:
            thresholds = tuple(
                PerformanceThreshold(
                    metric=str(row["metric"]), statistic=str(row["statistic"]),
                    operator=str(row["operator"]), limit=row["limit"],
                    expression=str(row.get("expression", "")),
                ) for row in spec["thresholds"]
            )
            performance_result = evaluate_k6_summary(
                payload, thresholds, exit_code=receipt.exit_code, timed_out=receipt.timed_out,
            )
            verdict, reason = performance_result.status, performance_result.reason
            performance = {
                "status": performance_result.status,
                "checked": list(performance_result.checked),
                "violations": list(performance_result.violations),
                "reason": performance_result.reason,
            }
    return {
        "test_id": test_id, "verdict": verdict, "reason": reason,
        "summary_ref": str(summary_path) if summary_path.is_file() else "",
        "command_receipt": {**receipt.__dict__, "passed": receipt.passed},
        "performance": performance,
        "image": str(spec["image"]),
    }


__all__ = ["K6ContractError", "build_k6_command", "run_k6_contract"]

