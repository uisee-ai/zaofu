"""Deterministic failure evidence bundle writer."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping


def build_failure_bundle(result: Mapping[str, Any]) -> dict[str, Any]:
    """Select only actionable failed/not-run commands and preserve run identity."""
    failures = [
        dict(row) for row in (result.get("commands") or [])
        if not row.get("passed")
    ]
    return {
        "schema_version": "product-test-failure-bundle.v1",
        "run_id": result.get("run_id"),
        "candidate_ref": result.get("candidate_ref"),
        "target_commit": result.get("target_commit"),
        "verdict": result.get("verdict"),
        "domains": [dict(row) for row in (result.get("domains") or [])],
        "failures": failures,
        "api_contract_failures": [
            dict(row)
            for row in (result.get("api_contracts") or [])
            if row.get("verdict") != "pass"
        ],
        "browser_test_failures": [
            dict(row)
            for row in (result.get("browser_tests") or [])
            if row.get("verdict") != "pass"
        ],
        "service_failures": [dict(row) for row in (result.get("services") or []) if not row.get("passed")],
        "container_failures": [
            dict(row) for row in (result.get("containers") or []) if not row.get("passed")
        ],
        "mock_service_failures": [
            dict(row) for row in (result.get("mock_services") or []) if not row.get("passed")
        ],
        "release_artifact_failures": [
            dict(row)
            for row in (result.get("release_artifacts") or [])
            if row.get("mandatory", True) and not row.get("passed")
        ],
        "security_scan_failures": [
            dict(row)
            for row in (result.get("security_scans") or [])
            if row.get("verdict") != "pass"
        ],
        "release_scenario_failures": [
            dict(row)
            for row in (result.get("release_scenarios") or [])
            if row.get("mandatory", True) and not row.get("passed")
        ],
        "recovery_scenario_failures": [
            dict(row)
            for row in (result.get("recovery_scenarios") or [])
            if row.get("mandatory", True) and not row.get("passed")
        ],
        "concurrency_scenario_failures": [
            dict(row)
            for row in (result.get("concurrency_scenarios") or [])
            if row.get("mandatory", True) and not row.get("passed")
        ],
        "persistence_scenario_failures": [
            dict(row)
            for row in (result.get("persistence_scenarios") or [])
            if row.get("mandatory", True) and not row.get("passed")
        ],
        "cleanup_failures": [dict(row) for row in (result.get("cleanup") or []) if not row.get("passed")],
        "missing_commands": list(result.get("missing_commands") or []),
        "failure_phase": result.get("failure_phase"),
        "errors": [dict(row) for row in (result.get("errors") or [])],
        "stability": dict(result.get("stability") or {}),
    }


def write_failure_bundle(result: Mapping[str, Any], path: Path) -> None:
    bundle = build_failure_bundle(result)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(bundle, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    temporary.replace(path)

