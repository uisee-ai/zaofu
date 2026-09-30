"""Baseline equivalence assertions for product-project recovery tests."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import json
import os
from pathlib import Path
import signal
import shlex
import time
from typing import Any, Mapping

from tests.testkit.processes import start_process
from tests.testkit.evidence import write_json_atomic


def freeze_state(state: Mapping[str, Any]) -> dict[str, Any]:
    """Return a detached JSON-like state copy suitable for comparison."""

    return deepcopy(dict(state))


def assert_recovery_equivalent(*, baseline: Mapping[str, Any], recovered: Mapping[str, Any], ignored_keys: tuple[str, ...] = ()) -> None:
    left = freeze_state(baseline)
    right = freeze_state(recovered)
    for key in ignored_keys:
        left.pop(key, None)
        right.pop(key, None)
    assert left == right, f"recovered state differs from baseline: {left} != {right}"


@dataclass(frozen=True)
class CrashRecoveryReceipt:
    command: tuple[str, ...]
    baseline: dict[str, Any]
    recovered: dict[str, Any]
    crash_returncode: int | None
    restarted: bool
    cleaned: bool
    error: str = ""

    @property
    def passed(self) -> bool:
        return (
            self.restarted and self.cleaned and not self.error
            and self.baseline == self.recovered
        )


def run_crash_restart(
    command: list[str] | tuple[str, ...],
    *,
    cwd: Path,
    state_path: Path,
    ready_path: Path,
    timeout_seconds: float = 10.0,
    env: Mapping[str, str] | None = None,
) -> CrashRecoveryReceipt:
    """Run a deterministic hard-stop/restart cycle and compare JSON state."""

    if not command or timeout_seconds <= 0:
        raise ValueError("command and positive timeout_seconds are required")
    first = None
    crash_returncode: int | None = None
    restarted = False
    cleaned = False
    error = ""
    second = None
    try:
        first = start_process(command, cwd=cwd, env=env)
        _wait_for_path(ready_path, timeout_seconds)
        baseline = _read_json_object(state_path)
        os.killpg(first.pid, signal.SIGKILL)  # type: ignore[union-attr]
        crash_returncode = first.wait(timeout=timeout_seconds)
        ready_path.unlink(missing_ok=True)
        second = start_process(command, cwd=cwd, env=env)
        _wait_for_path(ready_path, timeout_seconds)
        recovered = _read_json_object(state_path)
        restarted = True
    except (OSError, TimeoutError, ValueError, json.JSONDecodeError) as exc:
        baseline = locals().get("baseline", {})
        recovered = locals().get("recovered", {})
        error = f"{type(exc).__name__}: {exc}"
    finally:
        if second is not None:
            second.stop(timeout=1.0)
        if first is not None:
            first.stop(timeout=1.0)
        cleaned = all(process.returncode is not None for process in (first, second) if process is not None)
    return CrashRecoveryReceipt(
        command=tuple(str(item) for item in command), baseline=baseline, recovered=recovered,
        crash_returncode=crash_returncode, restarted=restarted, cleaned=cleaned, error=error,
    )


def run_recovery_scenarios(
    scenarios: tuple[Mapping[str, Any], ...] | list[Mapping[str, Any]],
    *,
    project_root: Path,
    output_dir: Path,
    env: Mapping[str, str] | None = None,
) -> tuple[dict[str, Any], ...]:
    """Execute declared hard-stop/restart scenarios and persist receipts."""

    results: list[dict[str, Any]] = []
    for spec in scenarios:
        scenario_id = str(spec["id"])
        receipt = run_crash_restart(
            shlex.split(str(spec["command"])),
            cwd=project_root,
            state_path=project_root / str(spec["state_path"]),
            ready_path=project_root / str(spec["ready_path"]),
            timeout_seconds=float(spec["timeout_seconds"]),
            env=env,
        )
        row = {**receipt.__dict__, "scenario_id": scenario_id, "passed": receipt.passed}
        row["verdict"] = "pass" if receipt.passed else (
            "environment_failure" if receipt.error else "fail"
        )
        row["command"] = list(receipt.command)
        row["receipt_ref"] = f"recovery.{scenario_id}.receipt.json"
        write_json_atomic(output_dir / str(row["receipt_ref"]), row)
        results.append(row)
    return tuple(results)


def _wait_for_path(path: Path, timeout_seconds: float) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if path.is_file():
            return
        time.sleep(0.01)
    raise TimeoutError(f"file did not appear: {path}")


def _read_json_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"state must be a JSON object: {path}")
    return value

