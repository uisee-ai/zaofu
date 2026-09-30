"""Opt-in serial native runner with before/after source observations."""

from __future__ import annotations

import json
import math
import os
from pathlib import Path
import subprocess
import sys
import xml.etree.ElementTree as ET
from typing import Any

from .evidence import evidence_ref, write_json_atomic
from .runner import run_pytest
from .source_observation import (
    SOURCE_SCOPE as SOURCE_SCOPE,
    capture_source as capture_source,
)


_TEST = "tests/conformance/test_zaofu_testkit_s5_native_claude_pipeline.py"
NATIVE_NODES = {
    "normal": _TEST + "::test_native_claude_start_dispatch_submit_verify_candidate",
    "impl": _TEST
    + "::test_native_claude_interrupted_stage_recovers_to_candidate[impl]",
    "verify": _TEST
    + "::test_native_claude_interrupted_stage_recovers_to_candidate[verify]",
}


def _native_pipeline_timeout_seconds() -> float:
    """Validate the bounded host-tier budget without importing host tests."""
    raw = os.environ.get("ZF_NATIVE_CLAUDE_PIPELINE_TIMEOUT_SECONDS", "720")
    try:
        value = float(raw)
    except ValueError as exc:
        raise ValueError(
            "ZF_NATIVE_CLAUDE_PIPELINE_TIMEOUT_SECONDS must be numeric"
        ) from exc
    if not math.isfinite(value) or not 0 < value <= 1800:
        raise ValueError(
            "ZF_NATIVE_CLAUDE_PIPELINE_TIMEOUT_SECONDS must be in (0, 1800]"
        )
    return value


def run_native_pipeline(
    *, project_root: Path, case: str, output: Path, timeout_seconds: float
) -> dict[str, Any]:
    """Execute one fixed native node through the normal phase-aware runner."""
    if os.environ.get("ZF_RUN_NATIVE_CLAUDE_PIPELINE", "").lower() not in {
        "1",
        "true",
        "yes",
    }:
        raise ValueError("native-pipeline requires ZF_RUN_NATIVE_CLAUDE_PIPELINE=1")
    if os.environ.get("PYTEST_ADDOPTS", "").strip():
        raise ValueError(
            "native-pipeline requires empty PYTEST_ADDOPTS for serial exact selection"
        )
    if case not in NATIVE_NODES:
        raise ValueError("unknown native case")
    case_budget = _native_pipeline_timeout_seconds()
    if (
        not math.isfinite(timeout_seconds)
        or not case_budget + 60 <= timeout_seconds <= 2100
    ):
        raise ValueError(
            "runner timeout must cover native budget + 60s cleanup and be <=2100s"
        )
    root = project_root.resolve()
    output = output.resolve()
    if output.is_relative_to(root):
        raise ValueError("native evidence must be outside the source checkout")
    output.mkdir(parents=True, exist_ok=False)
    payload: dict[str, Any] = {
        "schema_version": "zf-native-run.v1",
        "case": case,
        "nodeid": NATIVE_NODES[case],
        "verdict": "harness_failure",
        "gate_exit": 1,
        "source_stable_observed": False,
        "python_executable": sys.executable,
        "python_version": sys.version,
        "case_budget_seconds": case_budget,
        "runner_budget_seconds": timeout_seconds,
        "limitations": [
            "before/after observations do not prove no intervening edit-and-restore",
            "scope excludes user Provider configuration and installed dependency contents",
            "not release, full lifecycle or cross-run same-source acceptance",
        ],
    }
    try:
        before = capture_source(root)
        write_json_atomic(output / "source-before.json", before)
        payload["source_before"] = evidence_ref(
            output / "source-before.json", root=output
        )
        result = run_pytest(
            project_root=root,
            stage="NATIVE-PIPELINE",
            output_dir=output / "pytest",
            timeout_seconds=timeout_seconds,
            nodeids=(NATIVE_NODES[case],),
        )
        for key, path in (
            ("runner", output / "pytest/testkit-run.json"),
            ("phases", Path(result.report_path)),
            ("junit", Path(result.junit_path)),
        ):
            payload[key] = evidence_ref(path, root=output)
        after = capture_source(root)
        write_json_atomic(output / "source-after.json", after)
        payload["source_after"] = evidence_ref(
            output / "source-after.json", root=output
        )
        stable = (
            before["git_head"] == after["git_head"]
            and before["files_sha256"] == after["files_sha256"]
        )
        exact = [r["nodeid"] for r in result.cases] == [NATIVE_NODES[case]]
        cleanup = (
            ET.parse(result.junit_path)
            .getroot()
            .findall(
                ".//testcase/properties/property[@name='native_cleanup']",
            )
        )
        cleanup_complete = len(cleanup) == 1 and json.loads(
            cleanup[0].get("value", "null")
        ) == {
            "simulation_done": True,
            "tmux_stopped": True,
            "lock_released": True,
            "owned_pids_gone": True,
        }
        verdict = result.verdict if stable and exact else "harness_failure"
        if verdict == "pass" and not cleanup_complete:
            verdict = "harness_failure"
        payload.update(
            verdict=verdict,
            gate_exit=0 if verdict == "pass" else 1,
            source_stable_observed=stable,
            exact_selection=exact,
            cleanup_complete=cleanup_complete,
            pytest_exit_code=result.exit_code,
            session_finished=result.session_finished,
            timed_out=result.timed_out,
        )
    except (OSError, ValueError, subprocess.SubprocessError, ET.ParseError) as exc:
        payload["error"] = f"{type(exc).__name__}: {exc}"
    write_json_atomic(output / "native-run.json", payload)
    return payload
