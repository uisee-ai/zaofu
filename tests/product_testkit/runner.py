"""Unified product TestKit execution and evidence collection entrypoint."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Mapping, Any

from .executor import ProductHarnessError, _post_run_identity_error, run_manifest_file
from .failure_bundle import write_failure_bundle
from .report import (
    build_harness_failure_result,
    build_verification_result,
    write_junit_report,
    write_verification_result,
)
from .acceptance import AcceptanceReportError, build_acceptance_report
from .manifest import ManifestError
from .tiers import commands_for_ci_tier
from .artifacts import ArtifactError, build_artifact_index, capture_artifact, write_artifact_index
from .delivery import evaluate_delivery_readiness, iter_acceptance_evidence, write_delivery_readiness
from .stability import StabilityError, assess_stability, load_verification_result, write_stability_report
from tests.testkit.command_runner import run_command
from tests.testkit.evidence import write_json_atomic


def run_manifest_and_collect(
    manifest_path: Path,
    *,
    project_root: Path,
    output_dir: Path,
    run_id: str,
    candidate_ref: str | None = None,
    env: Mapping[str, str] | None = None,
    target_commit: str | None = None,
    acceptance_spec: Mapping[str, Any] | None = None,
    acceptance_spec_ref: str = "",
    acceptance_spec_digest: str = "",
    journey_results: tuple[Mapping[str, Any], ...] = (),
    ci_tier: str | None = None,
    prior_attempts: tuple[Mapping[str, Any], ...] = (),
) -> dict[str, Any]:
    """Run the canonical entrypoint and persist all machine-readable results."""
    from .manifest import load_manifest

    manifest = load_manifest(manifest_path)
    selected_ids = set(commands_for_ci_tier(manifest, ci_tier)) if ci_tier else None
    run = run_manifest_file(manifest_path, project_root=project_root, output_dir=output_dir, env=env,
                            selected_command_ids=selected_ids)
    cleanup_receipts = []
    for index, command in enumerate(manifest.raw.get("cleanup", [])):
        cleanup_receipts.append(run_command(
            command, command_id=f"cleanup.{index}", cwd=project_root,
            output_dir=output_dir, env=env, timeout_seconds=60.0,
        ))
    commit = target_commit or str(manifest.raw["target_commit"])
    # ``run_manifest_file`` checks identity after product commands, but cleanup
    # is intentionally executed by this outer runner.  Re-check after cleanup
    # so a cleanup hook cannot silently mutate tracked product bytes while the
    # command-level receipts remain green.
    identity_error = _post_run_identity_error(
        project_root, commit, verify_commit=True,
    )
    if identity_error:
        run = replace(run, harness_errors=(*run.harness_errors, identity_error))
    result = build_verification_result(manifest, run, target_commit=commit, run_id=run_id,
                                       candidate_ref=candidate_ref)
    stability_paths: list[Path] = []
    if prior_attempts:
        for index, attempt in enumerate(prior_attempts, start=1):
            attempt_path = output_dir / f"stability-attempt.{index:03d}.json"
            write_json_atomic(attempt_path, dict(attempt))
            stability_paths.append(attempt_path)
        try:
            stability = assess_stability((*prior_attempts, result))
        except StabilityError as exc:
            stability = {
                "schema_version": "product-stability-report.v1",
                "verdict": "harness_failure",
                "stable": False,
                "error": str(exc),
            }
            result["failure_phase"] = "stability"
            result["errors"] = [{"type": type(exc).__name__, "message": str(exc)}]
        result["stability"] = stability
        result["verdict"] = stability["verdict"]
        stability_path = output_dir / "stability-report.json"
        write_stability_report(stability, stability_path)
        stability_paths.append(stability_path)
    evidence_paths = [Path(row["stdout_ref"]) for row in result["commands"]]
    evidence_paths += [Path(row["stderr_ref"]) for row in result["commands"]]
    evidence_paths += [output_dir / f"{row['command_id']}.receipt.json" for row in result["commands"]]
    for service in result.get("services", []):
        evidence_paths += [
            Path(service["stdout_ref"]),
            Path(service["stderr_ref"]),
            Path(service["health_stdout_ref"]),
            Path(service["health_stderr_ref"]),
            output_dir / f"entrypoint.{service['entrypoint_id']}.receipt.json",
        ]
    for container in result.get("containers", []):
        for ref_name in (
            "stdout_ref", "stderr_ref", "health_stdout_ref", "health_stderr_ref", "receipt_ref",
        ):
            ref = Path(str(container.get(ref_name) or ""))
            if ref.is_file():
                evidence_paths.append(ref)
    for mock in result.get("mock_services", []):
        for ref_name in ("requests_ref", "receipt_ref"):
            ref = Path(str(mock.get(ref_name) or ""))
            if ref.is_file():
                evidence_paths.append(ref)
    for contract in result.get("api_contracts", []):
        for receipt_name in ("version_probe", "command_receipt"):
            receipt = contract.get(receipt_name)
            if not isinstance(receipt, Mapping):
                continue
            evidence_paths += [Path(str(receipt["stdout_ref"])), Path(str(receipt["stderr_ref"]))]
            evidence_paths.append(output_dir / f"{receipt['command_id']}.receipt.json")
        for ref_name in ("report_json_ref", "report_junit_ref"):
            path = Path(str(contract.get(ref_name) or ""))
            if path.is_file():
                evidence_paths.append(path)
    for browser in result.get("browser_tests", []):
        report_ref = Path(str(browser.get("report_ref") or ""))
        if report_ref.is_file():
            evidence_paths.append(report_ref)
        config_ref = Path(str(browser.get("config_ref") or ""))
        if config_ref.is_file():
            evidence_paths.append(config_ref)
        artifact_dir = Path(str(browser.get("artifact_dir") or ""))
        if artifact_dir.is_dir():
            evidence_paths.extend(
                path for path in sorted(artifact_dir.rglob("*"))
                if path.is_file() and not path.is_symlink()
            )
        receipt = browser.get("command_receipt")
        if isinstance(receipt, Mapping):
            evidence_paths += [Path(str(receipt["stdout_ref"])), Path(str(receipt["stderr_ref"]))]
            evidence_paths.append(output_dir / f"{receipt['command_id']}.receipt.json")
    for performance in result.get("performance_tests", []):
        summary_ref = Path(str(performance.get("summary_ref") or ""))
        if summary_ref.is_file():
            evidence_paths.append(summary_ref)
        receipt = performance.get("command_receipt")
        if isinstance(receipt, Mapping):
            evidence_paths += [Path(str(receipt["stdout_ref"])), Path(str(receipt["stderr_ref"]))]
            evidence_paths.append(Path(str(receipt["stdout_ref"])).with_name(f"{receipt['command_id']}.receipt.json"))
    for artifact in result.get("release_artifacts", []):
        artifact_ref = Path(str(artifact.get("artifact_ref") or ""))
        artifact_path = output_dir / artifact_ref
        if artifact_path.is_file():
            evidence_paths.append(artifact_path)
        receipt_ref = output_dir / str(artifact.get("receipt_ref") or "")
        if receipt_ref.is_file():
            evidence_paths.append(receipt_ref)
    for scan in result.get("security_scans", []):
        scan_dir = output_dir / "security" / str(scan.get("scan_id") or "")
        for ref_name in ("report_ref",):
            ref = Path(str(scan.get(ref_name) or ""))
            if (output_dir / ref).is_file():
                evidence_paths.append(output_dir / ref)
        result_ref = scan_dir / "result.json"
        if result_ref.is_file():
            evidence_paths.append(result_ref)
        receipt = scan.get("command_receipt")
        if isinstance(receipt, Mapping):
            evidence_paths += [Path(str(receipt["stdout_ref"])), Path(str(receipt["stderr_ref"]))]
            evidence_paths.append(scan_dir / f"{receipt['command_id']}.receipt.json")
    for scenario in result.get("release_scenarios", []):
        receipt_ref = output_dir / str(scenario.get("receipt_ref") or "")
        if receipt_ref.is_file():
            evidence_paths.append(receipt_ref)
        for step in scenario.get("steps") or []:
            for ref_name in ("stdout_ref", "stderr_ref"):
                ref = Path(str(step.get(ref_name) or ""))
                if ref.is_file():
                    evidence_paths.append(ref)
            command_id = str(step.get("command_id") or "")
            if command_id:
                step_receipt = receipt_ref.parent / f"{command_id}.receipt.json"
                if step_receipt.is_file():
                    evidence_paths.append(step_receipt)
    for key in ("recovery_scenarios", "concurrency_scenarios", "persistence_scenarios"):
        for scenario in result.get(key, []):
            receipt_ref = output_dir / str(scenario.get("receipt_ref") or "")
            if receipt_ref.is_file():
                evidence_paths.append(receipt_ref)
            for step in scenario.get("steps") or []:
                for ref_name in ("stdout_ref", "stderr_ref"):
                    ref = Path(str(step.get(ref_name) or ""))
                    if ref.is_file():
                        evidence_paths.append(ref)
                command_id = str(step.get("command_id") or "")
                if command_id and receipt_ref.is_file():
                    step_receipt = receipt_ref.parent / f"{command_id}.receipt.json"
                    if step_receipt.is_file():
                        evidence_paths.append(step_receipt)
    evidence_paths += [Path(receipt.stdout_ref) for receipt in cleanup_receipts]
    evidence_paths += [Path(receipt.stderr_ref) for receipt in cleanup_receipts]
    evidence_paths += [output_dir / f"{receipt.command_id}.receipt.json" for receipt in cleanup_receipts]
    evidence_paths += stability_paths
    result["artifact_index"] = build_artifact_index(evidence_paths, root=output_dir)
    result["cleanup"] = [{**receipt.__dict__, "passed": receipt.passed} for receipt in cleanup_receipts]
    if any(not receipt.passed for receipt in cleanup_receipts):
        result["verdict"] = "harness_failure"
    if result["artifact_index"]:
        write_artifact_index(result["artifact_index"], output_dir / "artifact-index.json")
    else:
        write_json_atomic(output_dir / "artifact-index.json", [])
    result["artifact_index_ref"] = "artifact-index.json"
    if ci_tier and not selected_ids:
        result["selection"] = {"ci_tier": ci_tier, "reason": "no_commands_for_requested_tier"}
    write_junit_report(result, output_dir / "junit.xml")
    write_failure_bundle(result, output_dir / "failure-bundle.json")
    write_verification_result(result, output_dir / "verification-result.json")
    if acceptance_spec is not None:
        acceptance = build_acceptance_report(
            spec=acceptance_spec,
            spec_ref=acceptance_spec_ref,
            spec_digest=acceptance_spec_digest,
            candidate_ref=candidate_ref or "",
            target_commit=commit,
            journey_results=journey_results,
        )
        acceptance_path = output_dir / "product-acceptance-report.json"
        temporary = acceptance_path.with_name(acceptance_path.name + ".tmp")
        temporary.write_text(
            __import__("json").dumps(acceptance, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8"
        )
        temporary.replace(acceptance_path)
        result["product_acceptance_report"] = acceptance
        if acceptance["verdict"] != "passed" and result["verdict"] == "pass":
            # A command-level green run is insufficient when a mandatory
            # product journey is rejected or blocked.
            result["verdict"] = "fail"
        indexed_refs = {str(row["ref"]) for row in result["artifact_index"]}
        for evidence in iter_acceptance_evidence(acceptance):
            ref = str(evidence.get("ref") or "")
            if not ref or ref in indexed_refs:
                continue
            try:
                captured = capture_artifact(Path(ref), root=output_dir)
            except ArtifactError:
                continue
            result["artifact_index"].append(captured)
            indexed_refs.add(captured["ref"])
        write_artifact_index(result["artifact_index"], output_dir / "artifact-index.json")
        readiness = evaluate_delivery_readiness(
            result,
            evidence_root=output_dir,
            expected_run_id=run_id,
            expected_candidate_ref=candidate_ref or "",
            expected_target_commit=commit,
            acceptance_required=True,
        )
        result["delivery_readiness"] = readiness
        write_delivery_readiness(readiness, output_dir / "delivery-readiness.json")
        if not readiness["allowed"] and result["verdict"] == "pass":
            result["verdict"] = "harness_failure"
            result["failure_phase"] = "delivery_readiness"
            result["errors"] = [{
                "type": "DeliveryReadinessError",
                "message": "; ".join(str(reason) for reason in readiness["reasons"]),
            }]
        # Acceptance is part of the canonical result, so refresh all derived
        # reports after attaching it; otherwise a stale failure bundle/JUnit
        # could disagree with verification-result.json.
        write_junit_report(result, output_dir / "junit.xml")
        write_failure_bundle(result, output_dir / "failure-bundle.json")
        write_verification_result(result, output_dir / "verification-result.json")
    return result


def run_product_closure(
    manifest_path: Path,
    *,
    project_root: Path,
    output_dir: Path,
    run_id: str,
    candidate_ref: str | None = None,
    env: Mapping[str, str] | None = None,
    target_commit: str | None = None,
    acceptance_spec: Mapping[str, Any] | None = None,
    acceptance_spec_ref: str = "",
    acceptance_spec_digest: str = "",
    journey_results: tuple[Mapping[str, Any], ...] = (),
    ci_tier: str | None = None,
    prior_attempts: tuple[Mapping[str, Any], ...] = (),
    prior_result_paths: tuple[Path, ...] = (),
) -> dict[str, Any]:
    """Return and persist a terminal verdict for every expected product input outcome."""

    try:
        loaded_prior_attempts = (
            *prior_attempts,
            *(load_verification_result(path) for path in prior_result_paths),
        )
        return run_manifest_and_collect(
            manifest_path,
            project_root=project_root,
            output_dir=output_dir,
            run_id=run_id,
            candidate_ref=candidate_ref,
            env=env,
            target_commit=target_commit,
            acceptance_spec=acceptance_spec,
            acceptance_spec_ref=acceptance_spec_ref,
            acceptance_spec_digest=acceptance_spec_digest,
            journey_results=journey_results,
            ci_tier=ci_tier,
            prior_attempts=loaded_prior_attempts,
        )
    except (
        ManifestError,
        ProductHarnessError,
        AcceptanceReportError,
        StabilityError,
        ArtifactError,
        OSError,
    ) as exc:
        if isinstance(exc, ManifestError):
            failure_phase = "admission"
        elif isinstance(exc, AcceptanceReportError):
            failure_phase = "acceptance"
        elif isinstance(exc, ProductHarnessError):
            failure_phase = "execution"
        elif isinstance(exc, StabilityError):
            failure_phase = "stability"
        else:
            failure_phase = "evidence"
        result = build_harness_failure_result(
            run_id=run_id,
            manifest_path=manifest_path,
            failure_phase=failure_phase,
            error=exc,
            candidate_ref=candidate_ref,
            target_commit=target_commit,
        )
        try:
            write_junit_report(result, output_dir / "junit.xml")
            write_failure_bundle(result, output_dir / "failure-bundle.json")
            write_verification_result(result, output_dir / "verification-result.json")
        except OSError as persistence_error:
            # stdout/the returned mapping remains the last-resort terminal
            # channel when the requested evidence root itself is unusable.
            result["evidence_persistence"] = {
                "status": "failed",
                "error": type(persistence_error).__name__,
                "message": str(persistence_error),
            }
        return result

