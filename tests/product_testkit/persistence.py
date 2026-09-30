"""Generic persistence assertions for product-project tests."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import shlex
from typing import Any, Mapping

from tests.testkit.command_runner import run_command
from tests.testkit.evidence import write_json_atomic


@dataclass(frozen=True)
class StateSnapshot:
    digest: str
    size: int


def snapshot_file(path: Path) -> StateSnapshot:
    if not path.is_file() or path.is_symlink():
        raise ValueError(f"state file must be a regular file: {path}")
    data = path.read_bytes()
    return StateSnapshot(hashlib.sha256(data).hexdigest(), len(data))


def assert_json_state(path: Path, expected: dict[str, Any]) -> None:
    actual = json.loads(path.read_text(encoding="utf-8"))
    assert actual == expected, (path, actual, expected)


def assert_idempotent_state(before: StateSnapshot, after: StateSnapshot) -> None:
    assert before == after, f"repeated operation changed persisted state: {before} -> {after}"


def run_persistence_scenarios(
    scenarios: tuple[Mapping[str, Any], ...] | list[Mapping[str, Any]],
    *,
    project_root: Path,
    output_dir: Path,
    env: Mapping[str, str] | None = None,
) -> tuple[dict[str, Any], ...]:
    """Run declared persistence/migration/restore commands in order."""

    results: list[dict[str, Any]] = []
    for spec in scenarios:
        scenario_id = str(spec["id"])
        scenario_dir = output_dir / "persistence" / scenario_id
        steps: list[dict[str, Any]] = []
        failed_index: int | None = None
        for index, command in enumerate(spec["commands"]):
            receipt = run_command(
                shlex.split(str(command)),
                command_id=f"persistence.{scenario_id}.{index}",
                cwd=project_root,
                output_dir=scenario_dir,
                env=env,
                timeout_seconds=float(spec["timeout_seconds"]),
            )
            steps.append({**receipt.__dict__, "passed": receipt.passed})
            if not receipt.passed:
                failed_index = index
                break
        failed_step = steps[-1] if failed_index is not None else None
        passed = failed_index is None and len(steps) == len(spec["commands"])
        row: dict[str, Any] = {
            "scenario_id": scenario_id,
            "mandatory": bool(spec.get("mandatory", True)),
            "deterministic": bool(spec.get("deterministic", True)),
            "passed": passed,
            "verdict": "pass" if passed else (
                "environment_failure"
                if failed_step and (failed_step.get("environment_error") or failed_step.get("timed_out"))
                else "fail"
            ),
            "steps": steps,
            "failed_index": failed_index,
        }
        if failed_step:
            row["error"] = str(failed_step.get("error") or "persistence command failed")
        row["receipt_ref"] = f"persistence.{scenario_id}.receipt.json"
        write_json_atomic(output_dir / str(row["receipt_ref"]), row)
        results.append(row)
    return tuple(results)

