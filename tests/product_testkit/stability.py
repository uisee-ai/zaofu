"""Cross-attempt stability oracle for isolated product test executions."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import json
from pathlib import Path
from typing import Any

from tests.testkit.evidence import write_json_atomic

from .verdict import ProductVerdict


class StabilityError(ValueError):
    """Attempt results cannot be compared safely."""


def load_verification_result(path: Path) -> dict[str, Any]:
    try:
        result = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_unique_object)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise StabilityError(f"cannot read prior verification result {path}: {exc}") from exc
    if not isinstance(result, dict) or result.get("schema_version") != "verification-result.v1":
        raise StabilityError(f"prior verification result has unsupported schema: {path}")
    return result


def assess_stability(attempts: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    rows = [dict(attempt) for attempt in attempts]
    if len(rows) < 2:
        raise StabilityError("stability assessment requires at least two attempts")
    for index, result in enumerate(rows):
        if result.get("schema_version") != "verification-result.v1":
            raise StabilityError(f"attempts[{index}] has unsupported schema")

    first = rows[0]
    for field in ("run_id", "candidate_ref", "target_commit"):
        expected = first.get(field)
        if not expected or any(result.get(field) != expected for result in rows[1:]):
            raise StabilityError(f"attempt {field} values do not match")

    plans = [_command_plan(result) for result in rows]
    if not plans[0] or any(plan != plans[0] for plan in plans[1:]):
        raise StabilityError("attempt command plans do not match")

    command_outcomes: dict[str, list[str]] = {command_id: [] for command_id, _ in plans[0]}
    for result in rows:
        by_id = {
            str(command.get("command_id") or ""): _command_outcome(command)
            for command in result.get("commands") or []
            if isinstance(command, Mapping)
        }
        by_id.update({
            str(contract.get("contract_id") or ""): str(contract.get("verdict") or "not_run")
            for contract in result.get("api_contracts") or []
            if isinstance(contract, Mapping)
        })
        by_id.update({
            str(browser.get("test_id") or ""): str(browser.get("verdict") or "not_run")
            for browser in result.get("browser_tests") or []
            if isinstance(browser, Mapping)
        })
        by_id.update({
            str(performance.get("test_id") or ""): str(performance.get("verdict") or "not_run")
            for performance in result.get("performance_tests") or []
            if isinstance(performance, Mapping)
        })
        by_id.update({
            str(artifact.get("artifact_id") or ""): "pass" if artifact.get("passed") else "fail"
            for artifact in result.get("release_artifacts") or []
            if isinstance(artifact, Mapping)
        })
        by_id.update({
            str(scan.get("scan_id") or ""): str(scan.get("verdict") or "not_run")
            for scan in result.get("security_scans") or []
            if isinstance(scan, Mapping)
        })
        by_id.update({
            str(scenario.get("scenario_id") or ""): str(scenario.get("verdict") or ("pass" if scenario.get("passed") else "fail"))
            for scenario in result.get("release_scenarios") or []
            if isinstance(scenario, Mapping)
        })
        for key in ("recovery_scenarios", "concurrency_scenarios", "persistence_scenarios"):
            by_id.update({
                str(scenario.get("scenario_id") or ""): str(scenario.get("verdict") or ("pass" if scenario.get("passed") else "fail"))
                for scenario in result.get(key) or []
                if isinstance(scenario, Mapping)
            })
        for command_id in command_outcomes:
            command_outcomes[command_id].append(by_id.get(command_id, "not_run"))
    flaky_commands = sorted(
        command_id
        for command_id, outcomes in command_outcomes.items()
        if set(outcomes) == {"fail", "pass"}
    )
    attempt_verdicts = [str(result.get("verdict") or "") for result in rows]
    verdict = _aggregate_verdict(attempt_verdicts, flaky=bool(flaky_commands))
    return {
        "schema_version": "product-stability-report.v1",
        "run_id": first["run_id"],
        "candidate_ref": first["candidate_ref"],
        "target_commit": first["target_commit"],
        "attempt_count": len(rows),
        "attempt_verdicts": attempt_verdicts,
        "command_plan": [
            {"command_id": command_id, "command_digest": digest}
            for command_id, digest in plans[0]
        ],
        "command_outcomes": command_outcomes,
        "flaky_commands": flaky_commands,
        "stable": not flaky_commands and len(set(attempt_verdicts)) == 1,
        "verdict": verdict,
    }


def _command_plan(result: Mapping[str, Any]) -> tuple[tuple[str, str], ...]:
    commands = result.get("command_plan", result.get("commands"))
    if not isinstance(commands, list):
        raise StabilityError("attempt commands must be a list")
    plan: list[tuple[str, str]] = []
    for command in commands:
        if not isinstance(command, Mapping):
            raise StabilityError("attempt command rows must be objects")
        command_id = str(command.get("command_id") or "")
        digest = str(command.get("command_digest") or "")
        if not command_id or not digest:
            raise StabilityError("attempt command plan requires command_id and command_digest")
        plan.append((command_id, digest))
    return tuple(plan)


def _command_outcome(command: Mapping[str, Any]) -> str:
    if command.get("environment_error") or command.get("timed_out"):
        return "environment_failure"
    return "pass" if command.get("passed") else "fail"


def _aggregate_verdict(verdicts: list[str], *, flaky: bool) -> str:
    if flaky:
        return ProductVerdict.FLAKY.value
    for verdict in (
        ProductVerdict.HARNESS_FAILURE,
        ProductVerdict.ENVIRONMENT_FAILURE,
        ProductVerdict.NOT_RUN,
        ProductVerdict.FAIL,
        ProductVerdict.PASS,
    ):
        if verdict.value in verdicts:
            return verdict.value
    raise StabilityError("attempt verdict is unsupported")


def write_stability_report(report: Mapping[str, Any], path: Path) -> None:
    write_json_atomic(path, dict(report))


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise StabilityError(f"duplicate JSON key in prior result: {key}")
        result[key] = value
    return result

