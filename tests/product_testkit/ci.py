"""Deterministic CI-tier orchestration for product TestKit runs."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from tests.testkit.evidence import write_json_atomic

from .runner import run_product_closure


_TIERS = ("T0", "T1", "T2", "T3")
_PRECEDENCE = {
    "harness_failure": 5,
    "environment_failure": 4,
    "flaky": 3,
    "not_run": 2,
    "fail": 1,
    "pass": 0,
}


def run_ci_matrix(
    manifest_path: Path,
    *,
    project_root: Path,
    output_dir: Path,
    run_id: str,
    tiers: tuple[str, ...] = _TIERS,
    candidate_ref: str | None = None,
    env: Mapping[str, str] | None = None,
    target_commit: str | None = None,
    acceptance_spec: Mapping[str, Any] | None = None,
    acceptance_spec_ref: str = "",
    acceptance_spec_digest: str = "",
    journey_results: tuple[Mapping[str, Any], ...] = (),
) -> dict[str, Any]:
    """Run selected cumulative tiers in isolated output directories.

    Every tier is retained even when an earlier tier fails, so the aggregate
    report distinguishes product failures from unavailable later tooling.
    """

    if not tiers or any(tier not in _TIERS for tier in tiers) or len(set(tiers)) != len(tiers):
        raise ValueError("tiers must be a non-empty sequence of unique T0..T3 values")
    ordered = tuple(sorted(tiers, key=_TIERS.index))
    tier_results: list[dict[str, Any]] = []
    for tier in ordered:
        tier_output = output_dir / tier
        result = run_product_closure(
            manifest_path,
            project_root=project_root,
            output_dir=tier_output,
            run_id=run_id,
            candidate_ref=candidate_ref,
            env=env,
            target_commit=target_commit,
            acceptance_spec=acceptance_spec,
            acceptance_spec_ref=acceptance_spec_ref,
            acceptance_spec_digest=acceptance_spec_digest,
            journey_results=journey_results,
            ci_tier=tier,
        )
        tier_results.append({
            "tier": tier,
            "verdict": result.get("verdict"),
            "output_dir": tier,
            "verification_result": tier_output / "verification-result.json",
            "junit": tier_output / "junit.xml",
            "failure_bundle": tier_output / "failure-bundle.json",
        })
    verdict = max(
        (str(row.get("verdict") or "not_run") for row in tier_results),
        key=lambda value: _PRECEDENCE.get(value, _PRECEDENCE["harness_failure"]),
    )
    summary = {
        "schema_version": "product-ci-matrix.v1",
        "run_id": run_id,
        "candidate_ref": candidate_ref,
        "target_commit": target_commit,
        "tiers": [dict(row) | {
            "output_dir": str(row["output_dir"]),
            "verification_result": str(row["verification_result"]),
            "junit": str(row["junit"]),
            "failure_bundle": str(row["failure_bundle"]),
        } for row in tier_results],
        "verdict": verdict,
        "all_requested_tiers_completed": all(
            (row["verification_result"]).is_file() for row in tier_results
        ),
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    write_json_atomic(output_dir / "ci-matrix.json", summary)
    return summary


__all__ = ["run_ci_matrix"]

