"""Verify real pytest-rerunfailures reports cannot hide flakiness."""

import json
import os
from pathlib import Path
import subprocess

from tests.testkit.parallel import merge_worker_reports
from tests.testkit.runner import _load_report, _relocate_worker_evidence


ROOT = Path(__file__).resolve().parents[2]
NODE = "tests/testkit/fixtures/rerun_case_fixture.py::test_fails_once_then_passes"
ORDINARY_NODE = "tests/testkit/fixtures/rerun_ordinary_fixture.py::test_ordinary_fails_once_then_passes"


def _nested_env() -> dict[str, str]:
    env = os.environ.copy()
    for name in ("PYTEST_XDIST_WORKER", "PYTEST_XDIST_TESTRUNUID", "PYTEST_XDIST_WORKER_COUNT"):
        env.pop(name, None)
    return env


def test_real_rerun_attempts_remain_non_green(tmp_path):
    report = tmp_path / "pytest-testkit.json"
    marker = tmp_path / "attempt.marker"
    command = [
        "uv", "run", "--with", "pytest-rerunfailures", "python", "-m", "pytest",
        "-p", "tests.testkit.pytest_plugin",
        "-o", "addopts=", "--reruns", "1", "--testkit-report", str(report),
        "-q", NODE,
    ]
    env = _nested_env()
    env["ZF_RERUN_MARKER"] = str(marker)
    completed = subprocess.run(command, cwd=ROOT, env=env, capture_output=True, text=True)
    if "unrecognized arguments: --reruns" in completed.stderr:
        raise AssertionError("pytest-rerunfailures is required for this contract")
    assert completed.returncode == 0, completed.stdout + completed.stderr
    payload = json.loads(report.read_text(encoding="utf-8"))
    [case] = payload["results"].values()
    assert payload["session_finished"]
    assert case["status"] == "flaky_pass"
    assert set(case["attempts"]) == {"0", "1"}
    assert case["attempts"]["0"]["call"]["outcome"] == "rerun"
    assert case["attempts"]["1"]["call"]["outcome"] == "passed"


def test_real_rerun_keeps_ordinary_evidence(tmp_path):
    report = tmp_path / "pytest-testkit.json"
    marker = tmp_path / "ordinary.marker"
    command = [
        "uv", "run", "--with", "pytest-rerunfailures", "python", "-m", "pytest",
        "-p", "tests.testkit.pytest_plugin", "-o", "addopts=", "--reruns", "1",
        "--testkit-report", str(report), "-q", ORDINARY_NODE,
    ]
    env = _nested_env()
    env["ZF_RERUN_MARKER"] = str(marker)
    completed = subprocess.run(command, cwd=ROOT, env=env, capture_output=True, text=True)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    payload = json.loads(report.read_text(encoding="utf-8"))
    [case] = payload["results"].values()
    assert case["status"] == "flaky_pass"
    assert case["ordinary_evidence"]["snapshots"]
    assert not case["ordinary_evidence"]["errors"]


def test_xdist_rerun_keeps_ordinary_evidence_and_flaky_status(tmp_path):
    output = tmp_path / "out"
    output.mkdir()
    report = output / "pytest-testkit.json"
    marker = output / "marker"
    command = [
        "uv", "run", "--with", "pytest-rerunfailures", "python", "-m", "pytest",
        "-p", "tests.testkit.pytest_plugin", "-o", "addopts=", "-n", "2",
        "--reruns", "1", "--testkit-report", str(report), "-q", ORDINARY_NODE,
    ]
    env = _nested_env()
    env["ZF_RERUN_MARKER"] = str(marker)
    completed = subprocess.run(command, cwd=ROOT, env=env, capture_output=True, text=True)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    worker_reports = sorted((output / "workers").glob("*/*/pytest-testkit.json"))
    payloads = [_load_report(path) for path in worker_reports]
    assert payloads and all(payload is not None for payload in payloads)
    for payload in payloads:
        _relocate_worker_evidence(payload, output)
    merged = merge_worker_reports(payloads)
    [case] = merged["results"].values()
    assert case["status"] == "flaky_pass"
    assert set(case["attempts"]) == {"0", "1"}
    assert case["ordinary_evidence"]["snapshots"]
