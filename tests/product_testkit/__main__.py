"""Command-line entrypoint for the generic product TestKit runner."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from .runner import run_product_closure
from .ci import run_ci_matrix
from .failure_bundle import write_failure_bundle
from .report import build_harness_failure_result, write_junit_report, write_verification_result


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m tests.product_testkit")
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--candidate-ref")
    parser.add_argument("--target-commit")
    parser.add_argument("--ci-tier", choices=("T0", "T1", "T2", "T3"))
    parser.add_argument("--ci-matrix", action="store_true", help="run isolated cumulative T0..T3 tiers")
    parser.add_argument("--prior-result", type=Path, action="append", default=[])
    parser.add_argument(
        "--acceptance-spec", type=Path,
        help="JSON Product Acceptance spec; requires --journey-results",
    )
    parser.add_argument(
        "--journey-results", type=Path,
        help="JSON list (or {journey_results: [...]}) produced by product journeys",
    )
    return parser


def _load_acceptance_inputs(
    spec_path: Path | None,
    journeys_path: Path | None,
) -> tuple[Mapping[str, Any] | None, str, str, tuple[Mapping[str, Any], ...]]:
    if journeys_path is not None and spec_path is None:
        raise ValueError("--journey-results requires --acceptance-spec")
    if spec_path is None:
        return None, "", "", ()
    raw = spec_path.read_bytes()
    spec = json.loads(raw.decode("utf-8"))
    if not isinstance(spec, Mapping):
        raise ValueError("Product Acceptance spec must be a JSON object")
    if journeys_path is None:
        raise ValueError("--acceptance-spec requires --journey-results")
    journeys_payload = json.loads(journeys_path.read_text(encoding="utf-8"))
    if isinstance(journeys_payload, Mapping):
        journeys_payload = journeys_payload.get("journey_results")
    if not isinstance(journeys_payload, list) or not all(
        isinstance(item, Mapping) for item in journeys_payload
    ):
        raise ValueError("journey results must be a JSON list or an object with journey_results")
    return (
        spec,
        str(spec_path.resolve()),
        hashlib.sha256(raw).hexdigest(),
        tuple(journeys_payload),
    )


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.ci_matrix and (args.ci_tier or args.prior_result):
        raise SystemExit("--ci-matrix cannot be combined with --ci-tier/--prior-result")
    try:
        acceptance_spec, acceptance_ref, acceptance_digest, journey_results = _load_acceptance_inputs(
            args.acceptance_spec, args.journey_results,
        )
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        # Keep malformed optional acceptance input on the same machine-readable
        # terminal path as manifest/execution failures; a CLI parse error must
        # not leave callers without verification evidence.
        error = ValueError(f"invalid Product Acceptance input: {exc}")
        result = build_harness_failure_result(
            run_id=args.run_id,
            manifest_path=args.manifest,
            failure_phase="acceptance",
            error=error,
            candidate_ref=args.candidate_ref,
            target_commit=args.target_commit,
        )
        try:
            write_junit_report(result, args.output_dir / "junit.xml")
            write_failure_bundle(result, args.output_dir / "failure-bundle.json")
            write_verification_result(result, args.output_dir / "verification-result.json")
        except OSError as persistence_error:
            result["evidence_persistence"] = {
                "status": "failed", "error": type(persistence_error).__name__,
                "message": str(persistence_error),
            }
        print(json.dumps({
            "verdict": result["verdict"], "run_id": result["run_id"],
            "error": result["errors"][0]["message"],
        }, sort_keys=True))
        return 1
    if args.ci_matrix:
        result = run_ci_matrix(
            args.manifest,
            project_root=args.project_root,
            output_dir=args.output_dir,
            run_id=args.run_id,
            candidate_ref=args.candidate_ref,
            target_commit=args.target_commit,
            acceptance_spec=acceptance_spec,
            acceptance_spec_ref=acceptance_ref,
            acceptance_spec_digest=acceptance_digest,
            journey_results=journey_results,
        )
    else:
        result = run_product_closure(
            args.manifest,
            project_root=args.project_root,
            output_dir=args.output_dir,
            run_id=args.run_id,
            candidate_ref=args.candidate_ref,
            target_commit=args.target_commit,
            acceptance_spec=acceptance_spec,
            acceptance_spec_ref=acceptance_ref,
            acceptance_spec_digest=acceptance_digest,
            journey_results=journey_results,
            ci_tier=args.ci_tier,
            prior_result_paths=tuple(args.prior_result),
        )
    summary = {"verdict": result["verdict"], "run_id": result["run_id"]}
    if result.get("errors"):
        summary["error"] = result["errors"][0]["message"]
    print(json.dumps(summary, sort_keys=True))
    return 0 if result["verdict"] == "pass" else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

