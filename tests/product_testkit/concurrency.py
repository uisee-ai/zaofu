"""Small deterministic assertions for product concurrency/idempotency tests."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
import subprocess
import shlex
import time
from typing import Sequence

from tests.testkit.processes import start_process
from tests.testkit.evidence import write_json_atomic


def assert_unique_keys(records: Iterable[Mapping[str, object]], *, key: str) -> None:
    values = [record.get(key) for record in records]
    duplicates = {value for value in values if values.count(value) > 1}
    assert not duplicates, f"duplicate business keys: {sorted(duplicates, key=str)}"


def assert_status_monotonic(statuses: Iterable[str], *, order: Mapping[str, int]) -> None:
    values = list(statuses)
    ranks = [order[item] for item in values]
    assert ranks == sorted(ranks), f"status regressed: {values}"


@dataclass(frozen=True)
class ConcurrentProcessResult:
    returncodes: tuple[int, ...]
    duration_seconds: float
    error: str = ""

    @property
    def passed(self) -> bool:
        return not self.error and all(code == 0 for code in self.returncodes)


def run_concurrent_processes(
    commands: Sequence[Sequence[str]], *, cwd: Path, timeout_seconds: float = 30.0,
    env: Mapping[str, str] | None = None,
) -> ConcurrentProcessResult:
    """Run independent product workers concurrently with bounded cleanup."""

    if not commands or timeout_seconds <= 0:
        raise ValueError("commands and positive timeout_seconds are required")
    started = time.monotonic()
    processes = []
    error = ""
    try:
        processes = [start_process(command, cwd=cwd, env=env) for command in commands]
        deadline = time.monotonic() + timeout_seconds
        while any(process.returncode is None for process in processes) and time.monotonic() < deadline:
            time.sleep(0.01)
        if any(process.returncode is None for process in processes):
            error = f"concurrent process timeout after {timeout_seconds}s"
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        error = f"{type(exc).__name__}: {exc}"
    finally:
        for process in processes:
            if process.returncode is None:
                process.stop(timeout=1.0)
    return ConcurrentProcessResult(
        returncodes=tuple(process.returncode if process.returncode is not None else -1 for process in processes),
        duration_seconds=round(time.monotonic() - started, 6),
        error=error,
    )


def run_concurrency_scenarios(
    scenarios: tuple[Mapping[str, object], ...] | list[Mapping[str, object]],
    *,
    project_root: Path,
    output_dir: Path,
    env: Mapping[str, str] | None = None,
) -> tuple[dict[str, object], ...]:
    """Run declared worker commands concurrently and write one receipt each."""

    results: list[dict[str, object]] = []
    for spec in scenarios:
        commands = [shlex.split(str(command)) for command in spec["commands"]]  # type: ignore[index]
        receipt = run_concurrent_processes(
            commands, cwd=project_root, timeout_seconds=float(spec["timeout_seconds"]), env=env,
        )
        row: dict[str, object] = {**receipt.__dict__, "scenario_id": str(spec["id"]), "passed": receipt.passed}
        row["verdict"] = "pass" if receipt.passed else (
            "environment_failure" if receipt.error else "fail"
        )
        row["returncodes"] = list(receipt.returncodes)
        row["commands"] = [list(command) for command in commands]
        row["receipt_ref"] = f"concurrency.{spec['id']}.receipt.json"
        write_json_atomic(output_dir / str(row["receipt_ref"]), row)
        results.append(row)
    return tuple(results)

