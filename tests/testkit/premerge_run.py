"""Source-bound execution of the complete pre-merge sentinel script."""

from __future__ import annotations

import math
import os
from pathlib import Path
import subprocess
import sys
import xml.etree.ElementTree as ET
from typing import Any

from .cleanup import terminate_process_group
from .evidence import evidence_ref, write_json_atomic
from .source_observation import SOURCE_SCOPE, capture_source


PREMERGE_SCOPE = (*SOURCE_SCOPE, "web", "docs")


def run_premerge(
    *, project_root: Path, output: Path, timeout_seconds: float = 120
) -> dict[str, Any]:
    root = project_root.resolve()
    output = output.resolve()
    if output.is_relative_to(root):
        raise ValueError("source-bound evidence must be outside the source checkout")
    if not math.isfinite(timeout_seconds) or not 0 < timeout_seconds <= 600:
        raise ValueError("premerge timeout must be between 0 and 600 seconds")
    output.mkdir(parents=True, exist_ok=False)
    payload: dict[str, Any] = {
        "schema_version": "zf-premerge-run.v1",
        "verdict": "harness_failure",
        "gate_exit": 1,
        "timed_out": False,
        "source_stable_observed": False,
        "python_executable": sys.executable,
        "project_root": str(root),
        "command": [],
    }
    before = capture_source(root, scope=PREMERGE_SCOPE)
    write_json_atomic(output / "source-before.json", before)
    payload["source_before"] = evidence_ref(output / "source-before.json", root=output)
    junit = output / "premerge-junit.xml"
    stdout = output / "premerge.stdout.log"
    stderr = output / "premerge.stderr.log"
    command = ("bash", "scripts/dev-premerge-gate.sh", f"--junitxml={junit}")
    payload["command"] = list(command)
    env = os.environ.copy()
    env["PYTEST_ADDOPTS"] = ""
    env["ZF_PYTHON"] = sys.executable
    for name in (
        "PYTEST_XDIST_WORKER",
        "PYTEST_XDIST_TESTRUNUID",
        "PYTEST_XDIST_WORKER_COUNT",
    ):
        env.pop(name, None)
    exit_code = None
    with stdout.open("w") as stdout_stream, stderr.open("w") as stderr_stream:
        try:
            process = subprocess.Popen(
                command,
                cwd=root,
                env=env,
                stdout=stdout_stream,
                stderr=stderr_stream,
                start_new_session=(os.name == "posix"),
                text=True,
            )
            try:
                exit_code = process.wait(timeout=timeout_seconds)
            except subprocess.TimeoutExpired:
                payload["timed_out"] = True
                terminate_process_group(process, timeout=3.0)
                exit_code = process.wait(timeout=5)
        except (OSError, subprocess.SubprocessError) as exc:
            payload["error"] = f"{type(exc).__name__}: {exc}"
    stable = False
    try:
        after = capture_source(root, scope=PREMERGE_SCOPE)
        write_json_atomic(output / "source-after.json", after)
        payload["source_after"] = evidence_ref(
            output / "source-after.json", root=output
        )
        stable = (
            before["git_head"] == after["git_head"]
            and before["files_sha256"] == after["files_sha256"]
        )
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        payload["error"] = f"{type(exc).__name__}: {exc}"
    payload["junit"] = evidence_ref(junit, root=output) if junit.is_file() else None
    payload["stdout"] = evidence_ref(stdout, root=output)
    payload["stderr"] = evidence_ref(stderr, root=output)
    junit_verdict = None
    if junit.is_file():
        from .native_acceptance import _premerge

        try:
            junit_verdict = _premerge(junit.read_bytes())["verdict"]
        except (OSError, ValueError, KeyError, TypeError, ET.ParseError) as exc:
            payload["error"] = f"{type(exc).__name__}: {exc}"
    verdict = (
        "harness_failure"
        if payload["timed_out"]
        or not stable
        or "error" in payload
        or (exit_code == 0 and not junit.is_file())
        else "pass"
        if exit_code == 0 and junit_verdict == "pass"
        else "fail"
    )
    payload.update(
        exit_code=exit_code,
        source_stable_observed=stable,
        verdict=verdict,
        gate_exit=0 if verdict == "pass" else 1,
    )
    write_json_atomic(output / "premerge-run.json", payload)
    return payload
