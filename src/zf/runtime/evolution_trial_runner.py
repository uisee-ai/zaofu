"""Isolated provider executor for evolution trial and canary requests."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.runtime.call_result_envelope import write_immutable_json_sidecar
from zf.runtime.evolution_contracts import EvolutionContractError, stable_digest
from zf.runtime.evolution_coordinator import EvolutionCoordinator
from zf.runtime.evolution_environment import (
    capture_evolution_environment,
    environment_archive_env,
    evaluate_evolution_environment,
)
from zf.runtime.evolution_evaluator import SealedEvaluatorAuthority
from zf.runtime.evolution_skill import materialize_skill_trial_arm
from zf.runtime.evolution_skill_campaign import verify_skill_attempt_evidence
from zf.runtime.evolution_skill_eval import classify_skill_treatment
from zf.runtime.evolution_skill_measurement import (
    persist_behavior_verdicts,
    score_provider_outputs,
)
from zf.runtime.evolution_skill_trajectory import (
    TRAJECTORY_SCHEMA,
    normalize_provider_trajectory,
)
from zf.runtime.evolution_skill_provider import (
    assert_skill_case_identity,
    codex_skill_isolation_args,
    skill_load_evidence,
    skill_trial_prompt,
)
from zf.runtime.run_archive import archive_run
from zf.runtime.sidecar_refs import hydrate_sidecar_ref


ProviderRunner = Callable[..., subprocess.CompletedProcess[str]]
EnvironmentSnapshotter = Callable[..., Mapping[str, Any]]

ENVIRONMENT_PREFLIGHT_COMPLETED = "evolution.environment.preflight.completed"
ENVIRONMENT_PREFLIGHT_FAILED = "evolution.environment.preflight.failed"


def execute_evolution_request(
    *,
    state_dir: Path,
    project_root: Path,
    config: Any,
    request_event_id: str,
    writer: EventWriter,
    runner: ProviderRunner = subprocess.run,
    environment_snapshotter: EnvironmentSnapshotter = capture_evolution_environment,
) -> dict[str, Any]:
    """Execute one EventLog request and settle it with immutable evidence."""

    state_dir = Path(state_dir).resolve(strict=False)
    project_root = Path(project_root).resolve(strict=False)
    request = next((
        event for event in writer.event_log.read_all()
        if event.id == request_event_id
    ), None)
    if request is None:
        raise EvolutionContractError(f"evolution request event not found: {request_event_id}")
    if request.type not in {"evolution.trial.requested", "evolution.canary.requested"}:
        raise EvolutionContractError(f"unsupported evolution request type: {request.type}")
    payload = _payload(request)
    campaign = _campaign(state_dir, payload)
    policy = getattr(getattr(config, "runtime", None), "evolution", None)
    if policy is None or not bool(getattr(policy, "enabled", False)):
        raise EvolutionContractError("runtime.evolution is disabled")
    backend = str(payload.get("backend") or getattr(policy, "backend", "")).strip()
    if backend not in {"codex", "claude-code"}:
        raise EvolutionContractError(f"unsupported evolution backend: {backend}")
    sealed_root = str(getattr(policy, "sealed_root", "") or "").strip()
    token_env = str(
        getattr(policy, "access_token_env", "ZF_EVOLUTION_EVALUATOR_TOKEN")
        or "ZF_EVOLUTION_EVALUATOR_TOKEN"
    )
    token = os.environ.get(token_env, "")
    if request.type == "evolution.trial.requested":
        return _execute_trial(
            state_dir=state_dir,
            project_root=project_root,
            request=request,
            campaign=campaign,
            token=token,
            token_env=token_env,
            sealed_root=sealed_root,
            backend=backend,
            writer=writer,
            runner=runner,
            environment_snapshotter=environment_snapshotter,
        )
    return _execute_canary(
        state_dir=state_dir,
        project_root=project_root,
        request=request,
        campaign=campaign,
        token=token,
        token_env=token_env,
        sealed_root=sealed_root,
        backend=backend,
        writer=writer,
        runner=runner,
        environment_snapshotter=environment_snapshotter,
    )


def _execute_trial(
    *,
    state_dir: Path,
    project_root: Path,
    request: ZfEvent,
    campaign: Mapping[str, Any],
    token: str,
    token_env: str,
    sealed_root: str,
    backend: str,
    writer: EventWriter,
    runner: ProviderRunner,
    environment_snapshotter: EnvironmentSnapshotter,
) -> dict[str, Any]:
    payload = _payload(request)
    coordinator = EvolutionCoordinator(state_dir, writer=writer)
    trial_id = str(payload.get("trial_id") or "")
    lease_owner = f"autoresearch-resident:{request.id}"
    future = (
        datetime.now(timezone.utc)
        + timedelta(seconds=int(campaign["attempt"]["execution_policy"]["lease_seconds"]))
    ).isoformat()
    claimed = coordinator.start_trial(
        trial_id,
        lease_owner=lease_owner,
        lease_expires_at=future,
        actor="autoresearch-evolution-runner",
    )
    row = claimed["trial"]
    if not claimed["claimed"]:
        return {
            "ok": row.get("status") == "settled",
            "status": "already_settled" if row.get("status") == "settled" else "not_claimed",
            "trial": row,
        }
    arm = str(row["arm"])
    preflight, preflight_ref = _record_environment_preflight(
        state_dir=state_dir,
        project_root=project_root,
        request=request,
        campaign=campaign,
        backend=backend,
        token_env=token_env,
        sealed_root=sealed_root,
        writer=writer,
        environment_snapshotter=environment_snapshotter,
    )
    if not bool(preflight["ok"]):
        return _settle_environment_failure(
            state_dir=state_dir,
            project_root=project_root,
            request=request,
            coordinator=coordinator,
            row=row,
            backend=backend,
            preflight=preflight,
            preflight_ref=preflight_ref,
        )
    authority = SealedEvaluatorAuthority(
        _sealed_root_path(project_root, sealed_root),
        access_token=token,
    )
    try:
        evidence = _run_evaluator_cases(
            state_dir=state_dir,
            request=request,
            campaign=campaign,
            evaluator=campaign["evaluator"],
            authority=authority,
            token=token,
            backend=backend,
            arm=arm,
            runner=runner,
        )
        archive = _archive_provider_evidence(
            state_dir=state_dir,
            project_root=project_root,
            request=request,
            evidence=evidence,
            run_id=f"evo-{trial_id}-a{row['attempt_number']}",
            backend=backend,
            arm=arm,
            environment_preflight=preflight,
            environment_preflight_ref=preflight_ref,
        )
        measurement = _measurement(
            campaign,
            evaluator=campaign["evaluator"],
            trial_id=trial_id,
            arm=arm,
            replicate=int(row["replicate"]),
            evaluation=evidence["evaluation"],
            treatment=evidence.get("treatment"),
            archive_ref=str(archive["manifest"]),
            archive_digest=str(archive["digest"]),
        )
        cost_ref = write_immutable_json_sidecar(
            state_dir,
            {
                "schema_version": "evolution-cost-receipt.v1",
                "trial_id": trial_id,
                "provider": backend,
                "usage": evidence["usage"],
            },
            root="evolution/cost-receipts",
            kind="evolution_cost_receipt",
            schema_version="evolution-cost-receipt.v1",
            created_by="autoresearch-evolution-runner",
            source_event_id=request.id,
        )
        settled = coordinator.settle_trial(
            trial_id,
            lease_owner=lease_owner,
            attempt_number=int(row["attempt_number"]),
            outcome="passed",
            evaluator_generation=campaign["evaluator"],
            measurement=measurement,
            archive_ref=str(archive["manifest"]),
            archive_digest=str(archive["digest"]),
            cost_receipt_refs=[str(cost_ref["ref"])],
            actor="autoresearch-evolution-runner",
        )
        return {"ok": True, "status": "settled", **settled, "provider": evidence}
    except Exception as exc:
        archive = _archive_infrastructure_failure(
            state_dir=state_dir,
            project_root=project_root,
            request=request,
            run_id=f"evo-{trial_id}-a{row['attempt_number']}-failed",
            backend=backend,
            reason=f"{type(exc).__name__}: {exc}",
            environment_preflight=preflight,
            environment_preflight_ref=preflight_ref,
        )
        settled = coordinator.settle_trial(
            trial_id,
            lease_owner=lease_owner,
            attempt_number=int(row["attempt_number"]),
            outcome="infrastructure_failed",
            archive_ref=str(archive["manifest"]),
            archive_digest=str(archive["digest"]),
            failure_class=type(exc).__name__,
            actor="autoresearch-evolution-runner",
        )
        return {
            "ok": False,
            "status": "retryable",
            "reason": str(exc),
            **settled,
        }


def _execute_canary(
    *,
    state_dir: Path,
    project_root: Path,
    request: ZfEvent,
    campaign: Mapping[str, Any],
    token: str,
    token_env: str,
    sealed_root: str,
    backend: str,
    writer: EventWriter,
    runner: ProviderRunner,
    environment_snapshotter: EnvironmentSnapshotter,
) -> dict[str, Any]:
    payload = _payload(request)
    evaluator = campaign.get("canary_evaluator")
    if not isinstance(evaluator, Mapping) or not evaluator:
        raise EvolutionContractError("campaign has no independent canary evaluator")
    asset_id = str(payload.get("asset_id") or "")
    version = int(payload.get("version") or 0)
    preflight, preflight_ref = _record_environment_preflight(
        state_dir=state_dir,
        project_root=project_root,
        request=request,
        campaign=campaign,
        backend=backend,
        token_env=token_env,
        sealed_root=sealed_root,
        writer=writer,
        environment_snapshotter=environment_snapshotter,
    )
    if not bool(preflight["ok"]):
        archive = _archive_infrastructure_failure(
            state_dir=state_dir,
            project_root=project_root,
            request=request,
            run_id="evocanary-" + stable_digest({
                "asset_id": asset_id,
                "version": version,
                "request": request.id,
                "preflight": preflight_ref["sha256"],
            })[:20],
            backend=backend,
            reason=str(preflight["failure_class"]),
            environment_preflight=preflight,
            environment_preflight_ref=preflight_ref,
        )
        event = writer.emit(
            "evolution.canary.failed",
            actor="autoresearch-evolution-runner",
            causation_id=request.id,
            correlation_id=str(campaign["campaign_id"]),
            payload={
                "schema_version": "evolution-canary-failure.v1",
                "campaign_id": campaign["campaign_id"],
                "asset_id": asset_id,
                "version": version,
                "failure_class": str(preflight["failure_class"]),
                "reason": _preflight_reason(preflight),
                "retryable": False,
                "environment_preflight_ref": preflight_ref,
                "archive_ref": archive["manifest"],
                "archive_digest": archive["digest"],
            },
        )
        return {
            "ok": False,
            "status": "environment_failed",
            "event_id": event.id,
            "failure_class": preflight["failure_class"],
            "environment_preflight_ref": preflight_ref,
        }
    authority = SealedEvaluatorAuthority(
        _sealed_root_path(project_root, sealed_root),
        access_token=token,
    )
    try:
        evidence = _run_evaluator_cases(
            state_dir=state_dir,
            request=request,
            campaign=campaign,
            evaluator=evaluator,
            authority=authority,
            token=token,
            backend=backend,
            arm="candidate",
            runner=runner,
        )
        archive = _archive_provider_evidence(
            state_dir=state_dir,
            project_root=project_root,
            request=request,
            evidence=evidence,
            run_id="evocanary-" + stable_digest({
                "asset_id": asset_id,
                "version": version,
                "request": request.id,
            })[:20],
            backend=backend,
            arm="candidate",
            environment_preflight=preflight,
            environment_preflight_ref=preflight_ref,
        )
        passed = bool(evidence["evaluation"]["gate_passed"])
        outcome = "passed" if passed else "regressed"
        event = writer.emit(
            "evolution.canary.completed",
            actor="autoresearch-evolution-runner",
            causation_id=request.id,
            correlation_id=str(campaign["campaign_id"]),
            payload={
                "schema_version": "evolution-canary-result.v1",
                "campaign_id": campaign["campaign_id"],
                "asset_id": asset_id,
                "version": version,
                "outcome": outcome,
                "usage_ref": f"run-archive://{archive['digest']}",
                "archive_ref": archive["manifest"],
                "archive_digest": archive["digest"],
                "cost": evidence["usage"],
                "cohort": {
                    "task_family": campaign["attempt"]["objective"]["task_family"],
                    "provider": backend,
                    "model": str(payload.get("model") or "provider-default"),
                },
                "evaluation": {
                    "baseline_ref": "",
                    "candidate_ref": "",
                    "holdout_matched": True,
                },
                "score": evidence["evaluation"]["total_score"],
            },
        )
        return {"ok": True, "status": outcome, "event_id": event.id}
    except Exception as exc:
        event = writer.emit(
            "evolution.canary.failed",
            actor="autoresearch-evolution-runner",
            causation_id=request.id,
            correlation_id=str(campaign["campaign_id"]),
            payload={
                "schema_version": "evolution-canary-failure.v1",
                "campaign_id": campaign["campaign_id"],
                "asset_id": asset_id,
                "version": version,
                "failure_class": type(exc).__name__,
                "reason": str(exc),
                "retryable": True,
                "environment_preflight_ref": preflight_ref,
            },
        )
        return {"ok": False, "status": "failed", "event_id": event.id, "reason": str(exc)}


def _run_evaluator_cases(
    *,
    state_dir: Path,
    request: ZfEvent,
    campaign: Mapping[str, Any],
    evaluator: Mapping[str, Any],
    authority: SealedEvaluatorAuthority,
    token: str,
    backend: str,
    arm: str,
    runner: ProviderRunner,
) -> dict[str, Any]:
    outputs: list[dict[str, Any]] = []
    usage: dict[str, float] = {"input_tokens": 0.0, "output_tokens": 0.0}
    attempt = campaign.get("attempt")
    if not isinstance(attempt, Mapping):
        raise EvolutionContractError("evolution campaign has no attempt")
    skill_spec = verify_skill_attempt_evidence(state_dir, attempt)

    def trusted(cases: list[dict[str, Any]]) -> Mapping[str, Any]:
        if skill_spec:
            assert_skill_case_identity(skill_spec, cases)
        for index, case in enumerate(cases, start=1):
            result = _invoke_provider(
                request=request,
                campaign=campaign,
                case=case,
                state_dir=state_dir,
                backend=backend,
                arm=arm,
                index=index,
                runner=runner,
                skill_spec=skill_spec,
            )
            trajectory_ref = write_immutable_json_sidecar(
                state_dir,
                result["trajectory"],
                root="evolution/skill-trajectories",
                kind="skill_provider_trajectory",
                schema_version=TRAJECTORY_SCHEMA,
                created_by="autoresearch-evolution-runner",
                source_event_id=request.id,
            )
            result["trajectory_ref"] = trajectory_ref
            outputs.append(result)
            for key in ("input_tokens", "output_tokens"):
                usage[key] += float((result.get("usage") or {}).get(key) or 0)
        return score_provider_outputs(outputs, cases, evaluator)

    evaluation = authority.evaluate(
        str(evaluator["holdout_authority_ref"]),
        generation_digest=str(evaluator["generation_digest"]),
        access_token=token,
        trusted_runner=trusted,
    )
    persist_behavior_verdicts(
        state_dir=state_dir,
        request=request,
        outputs=outputs,
        evaluation=evaluation,
    )
    result: dict[str, Any] = {
        "outputs": outputs,
        "usage": {key: int(value) for key, value in usage.items()},
        "evaluation": evaluation,
    }
    if skill_spec:
        semantic_arm = str(skill_spec["arm_map"][arm])
        identity = skill_spec["treatment_identities"][semantic_arm]
        loaded_case_ids = [
            str(item["case_id"])
            for item in outputs
            if bool(item.get("target_skill_loaded"))
        ]
        behavior_by_case = {
            str(item["case_id"]): item["behavior_followed"]
            for item in evaluation.get("case_results") or []
            if isinstance(item.get("behavior_followed"), bool)
        }
        treatment = classify_skill_treatment(
            identity=identity,
            cases=skill_spec["eval_suite"]["cases"],
            loaded_case_ids=loaded_case_ids,
            behavior_by_case=behavior_by_case,
        )
        result["treatment"] = treatment
        result["materializations"] = [
            dict(item.get("skill_materialization") or {}) for item in outputs
        ]
    return result


def _invoke_provider(
    *,
    state_dir: Path | None = None,
    request: ZfEvent,
    campaign: Mapping[str, Any],
    case: Mapping[str, Any],
    backend: str,
    arm: str,
    index: int,
    runner: ProviderRunner,
    skill_spec: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    work = Path(tempfile.mkdtemp(prefix="zf-evolution-provider-"))
    try:
        output = work / "final.txt"
        skill_materialization: dict[str, Any] = {}
        if skill_spec:
            skill_materialization = materialize_skill_trial_arm(
                workdir=work,
                backend=backend,
                spec=skill_spec,
                arm=arm,
            )
            prompt = skill_trial_prompt(skill_spec, case)
        else:
            candidate_method = (
                str(campaign["asset"]["content"])
                if arm == "candidate" else "No candidate method is available."
            )
            prompt = (
                "Solve the evaluation task without reading files or using tools. "
                "Return only the requested answer; do not mention this evaluation.\n\n"
                f"Method available:\n{candidate_method}\n\n"
                f"Task:\n{str(case.get('prompt') or '')}"
            )
        model = str(_payload(request).get("model") or "")
        effort = str(_payload(request).get("model_reasoning_effort") or "")
        timeout = int(_payload(request).get("timeout_seconds") or 300)
        environment = os.environ.copy()
        if backend == "codex":
            command = [
                "codex", "exec", "--json", "--ephemeral", "--ignore-user-config",
                "--sandbox", "danger-full-access", "--skip-git-repo-check",
                "--output-last-message", str(output),
                *codex_skill_isolation_args(environment=environment),
                *( ["--model", model] if model else [] ),
                *( ["--config", f'model_reasoning_effort="{effort}"'] if effort else [] ),
                "-C", str(work), prompt,
            ]
        else:
            command = [
                "claude", "-p", "--permission-mode", "bypassPermissions",
                "--output-format", "json",
                *( ["--model", model] if model else [] ),
                prompt,
            ]
        from zf.integrations.controller.helper import run_provider
        proc = run_provider(
            command, backend=backend, state_dir=state_dir, model=model, runner=runner,
            cwd=work,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
        )
        if proc.returncode != 0:
            raise RuntimeError(
                f"{backend} exited {proc.returncode}: {(proc.stderr or '')[-1000:]}"
            )
        if backend == "codex":
            final = output.read_text(encoding="utf-8") if output.exists() else ""
            audit = _codex_audit(proc.stdout or "")
        else:
            final, audit = _claude_result(proc.stdout or "")
        if not final.strip():
            raise RuntimeError("provider returned no final answer")
        target_path_raw = str(skill_materialization.get("target_path") or "").strip()
        target_path = Path(target_path_raw) if target_path_raw else None
        if target_path is not None and not target_path.is_absolute():
            target_path = work / target_path
        load_evidence = skill_load_evidence(
            stdout=proc.stdout or "",
            stderr=proc.stderr or "",
            skill_name=str(skill_spec.get("skill_name") or "") if skill_spec else "",
            target_path=str(target_path) if target_path is not None else "",
            backend=backend,
            prompt=prompt,
        )
        manifest_payload: dict[str, Any] = {}
        manifest_path = Path(str(skill_materialization.get("manifest_path") or ""))
        if manifest_path and not manifest_path.is_absolute():
            manifest_path = work / manifest_path
        if skill_spec and manifest_path.is_file():
            loaded_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            if isinstance(loaded_manifest, dict):
                manifest_payload = loaded_manifest
        trajectory = normalize_provider_trajectory(
            case_id=str(case.get("case_id") or f"case-{index}"),
            backend=backend,
            stdout=proc.stdout or "",
            stderr=proc.stderr or "",
            final=final.strip(),
            skill_load_evidence=load_evidence,
            workspace_root=str(work),
        )
        return {
            "case_index": index,
            "case_id": str(case.get("case_id") or f"case-{index}"),
            "final": final.strip(),
            "provider_session_id": audit["provider_session_id"],
            "usage": audit["usage"],
            "stdout": proc.stdout or "",
            "stderr": proc.stderr or "",
            "target_skill_loaded": bool(load_evidence),
            "skill_load_evidence": load_evidence,
            "trajectory": trajectory,
            "skill_materialization": {
                **skill_materialization,
                "manifest": manifest_payload,
            } if skill_spec else {},
        }
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _measurement(
    campaign: Mapping[str, Any],
    *,
    evaluator: Mapping[str, Any],
    trial_id: str,
    arm: str,
    replicate: int,
    evaluation: Mapping[str, Any],
    treatment: object = None,
    archive_ref: str = "",
    archive_digest: str = "",
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "schema_version": "evolution-measurement.v1",
        "trial_id": trial_id,
        "arm": arm,
        "evaluator_generation_digest": evaluator["generation_digest"],
        "comparison_identity": dict(campaign["comparison_identity"]),
        "gates": dict(evaluation["gates"]),
        "scores": dict(evaluation["scores"]),
    }
    if isinstance(treatment, Mapping):
        body.update({
            "treatment": dict(treatment),
            "case_results": list(evaluation.get("case_results") or []),
            "pairing": {"replicate": int(replicate)},
            "evidence_archive_ref": archive_ref,
            "evidence_archive_digest": archive_digest,
        })
    return body


def _record_environment_preflight(
    *,
    state_dir: Path,
    project_root: Path,
    request: ZfEvent,
    campaign: Mapping[str, Any],
    backend: str,
    token_env: str,
    sealed_root: str,
    writer: EventWriter,
    environment_snapshotter: EnvironmentSnapshotter,
) -> tuple[dict[str, Any], dict[str, Any]]:
    attempt = campaign.get("attempt")
    if not isinstance(attempt, Mapping):
        raise EvolutionContractError("evolution campaign lacks attempt for environment preflight")
    frozen = attempt.get("frozen_inputs")
    if not isinstance(frozen, Mapping):
        raise EvolutionContractError("evolution attempt lacks frozen environment inputs")
    snapshot = dict(environment_snapshotter(
        project_root=project_root,
        state_dir=state_dir,
        backend=backend,
        model=str(_payload(request).get("model") or ""),
        reasoning_effort=str(
            _payload(request).get("model_reasoning_effort") or ""
        ),
        token_env=token_env,
        sealed_root=sealed_root,
    ))
    preflight = evaluate_evolution_environment(snapshot, frozen_inputs=frozen)
    descriptor = write_immutable_json_sidecar(
        state_dir,
        preflight,
        root="evolution/environment-preflight",
        kind="evolution_environment_preflight",
        schema_version="evolution-environment-preflight.v1",
        created_by="autoresearch-evolution-runner",
        source_event_id=request.id,
    )
    payload = _payload(request)
    writer.emit(
        (
            ENVIRONMENT_PREFLIGHT_COMPLETED
            if bool(preflight["ok"])
            else ENVIRONMENT_PREFLIGHT_FAILED
        ),
        actor="autoresearch-evolution-runner",
        causation_id=request.id,
        correlation_id=str(campaign.get("campaign_id") or ""),
        payload={
            "schema_version": "evolution-environment-preflight-event.v1",
            "request_event_id": request.id,
            "campaign_id": str(campaign.get("campaign_id") or ""),
            "trial_id": str(payload.get("trial_id") or ""),
            "asset_id": str(payload.get("asset_id") or ""),
            "version": int(payload.get("version") or 0),
            "status": str(preflight["status"]),
            "failure_class": str(preflight["failure_class"]),
            "retryable": False,
            "environment_preflight_ref": descriptor,
            "observed_digests": dict(preflight["observed_digests"]),
            "expected_digests": dict(preflight["expected_digests"]),
        },
    )
    return preflight, descriptor


def _settle_environment_failure(
    *,
    state_dir: Path,
    project_root: Path,
    request: ZfEvent,
    coordinator: EvolutionCoordinator,
    row: Mapping[str, Any],
    backend: str,
    preflight: Mapping[str, Any],
    preflight_ref: Mapping[str, Any],
) -> dict[str, Any]:
    archive = _archive_infrastructure_failure(
        state_dir=state_dir,
        project_root=project_root,
        request=request,
        run_id=(
            f"evo-{row['trial_id']}-a{row['attempt_number']}-environment"
        ),
        backend=backend,
        reason=_preflight_reason(preflight),
        environment_preflight=preflight,
        environment_preflight_ref=preflight_ref,
    )
    settled = coordinator.settle_trial(
        str(row["trial_id"]),
        lease_owner=f"autoresearch-resident:{request.id}",
        attempt_number=int(row["attempt_number"]),
        outcome="infrastructure_failed",
        archive_ref=str(archive["manifest"]),
        archive_digest=str(archive["digest"]),
        failure_class=str(preflight["failure_class"]),
        retryable=False,
        actor="autoresearch-evolution-runner",
    )
    return {
        "ok": False,
        "status": "environment_failed",
        "reason": _preflight_reason(preflight),
        "failure_class": str(preflight["failure_class"]),
        "environment_preflight_ref": dict(preflight_ref),
        **settled,
    }


def _preflight_reason(preflight: Mapping[str, Any]) -> str:
    for check in preflight.get("checks") or []:
        if isinstance(check, Mapping) and not bool(check.get("ok")):
            return str(check.get("detail") or check.get("failure_class") or "environment preflight failed")
    return str(preflight.get("failure_class") or "environment preflight failed")


def _sealed_root_path(project_root: Path, sealed_root: str) -> Path:
    path = Path(sealed_root).expanduser()
    return path if path.is_absolute() else project_root / path


def _archive_provider_evidence(
    *,
    state_dir: Path,
    project_root: Path,
    request: ZfEvent,
    evidence: Mapping[str, Any],
    run_id: str,
    backend: str,
    arm: str,
    environment_preflight: Mapping[str, Any] | None = None,
    environment_preflight_ref: Mapping[str, Any] | None = None,
) -> dict[str, str]:
    live = state_dir / "evolution" / "live" / run_id
    live.mkdir(parents=True, exist_ok=True)
    outputs = live / "provider-outputs.json"
    outputs.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    events_path = live / "events.jsonl"
    events_path.write_text(
        "".join(str(item.get("stdout") or "") for item in evidence["outputs"]),
        encoding="utf-8",
    )
    (live / "cost.jsonl").write_text(
        json.dumps({"backend": backend, **dict(evidence["usage"])}) + "\n",
        encoding="utf-8",
    )
    preflight_path = _write_environment_preflight_artifact(
        live,
        preflight=environment_preflight,
        descriptor=environment_preflight_ref,
    )
    supplemental_files: dict[str, Path] = {"provider-outputs.json": outputs}
    if preflight_path is not None:
        supplemental_files["environment-preflight.json"] = preflight_path
    archive = archive_run(
        project_root=project_root,
        state_dir=state_dir,
        live_state_dir=live,
        run_id=run_id,
        status="passed",
        command=f"{backend} isolated evolution {arm}",
        provider={
            "provider": backend,
            "model": str(_payload(request).get("model") or "provider-default"),
            "usage": dict(evidence["usage"]),
            "session_ids": [
                str(item.get("provider_session_id") or "")
                for item in evidence["outputs"]
            ],
        },
        summary={
            "arm": arm,
            "evolution": True,
            "request_event_id": request.id,
            "environment_preflight_ref": dict(environment_preflight_ref or {}),
        },
        supplemental_files=supplemental_files,
        env=(environment_archive_env(environment_preflight)
             if environment_preflight is not None else {}),
    )
    shutil.rmtree(live, ignore_errors=True)
    return {"manifest": str(archive.manifest_path), "digest": archive.manifest_digest}


def _archive_infrastructure_failure(
    *,
    state_dir: Path,
    project_root: Path,
    request: ZfEvent,
    run_id: str,
    backend: str,
    reason: str,
    environment_preflight: Mapping[str, Any] | None = None,
    environment_preflight_ref: Mapping[str, Any] | None = None,
) -> dict[str, str]:
    live = state_dir / "evolution" / "live" / run_id
    live.mkdir(parents=True, exist_ok=True)
    (live / "events.jsonl").write_text("", encoding="utf-8")
    failure = live / "failure.json"
    failure.write_text(json.dumps({"reason": reason}) + "\n", encoding="utf-8")
    preflight_path = _write_environment_preflight_artifact(
        live,
        preflight=environment_preflight,
        descriptor=environment_preflight_ref,
    )
    supplemental_files: dict[str, Path] = {"failure.json": failure}
    if preflight_path is not None:
        supplemental_files["environment-preflight.json"] = preflight_path
    archive = archive_run(
        project_root=project_root,
        state_dir=state_dir,
        live_state_dir=live,
        run_id=run_id,
        status="failed",
        command=f"{backend} isolated evolution",
        exit_code=1,
        provider={"provider": backend},
        summary={
            "reason": reason,
            "request_event_id": request.id,
            "environment_preflight_ref": dict(environment_preflight_ref or {}),
        },
        supplemental_files=supplemental_files,
        env=(environment_archive_env(environment_preflight)
             if environment_preflight is not None else {}),
    )
    shutil.rmtree(live, ignore_errors=True)
    return {"manifest": str(archive.manifest_path), "digest": archive.manifest_digest}


def _write_environment_preflight_artifact(
    live: Path,
    *,
    preflight: Mapping[str, Any] | None,
    descriptor: Mapping[str, Any] | None,
) -> Path | None:
    if preflight is None:
        return None
    path = live / "environment-preflight.json"
    path.write_text(
        json.dumps(
            {
                "preflight": dict(preflight),
                "sidecar_ref": dict(descriptor or {}),
            },
            indent=2,
            sort_keys=True,
        ) + "\n",
        encoding="utf-8",
    )
    return path


def _campaign(state_dir: Path, payload: Mapping[str, Any]) -> dict[str, Any]:
    descriptor = payload.get("campaign_ref")
    if not isinstance(descriptor, Mapping):
        raise EvolutionContractError("evolution request lacks campaign_ref")
    hydrated = hydrate_sidecar_ref(
        state_dir,
        dict(descriptor),
        purpose="evolution-provider-execution",
        actor="autoresearch-evolution-runner",
    )
    if not isinstance(hydrated.payload, Mapping):
        raise EvolutionContractError("evolution campaign is invalid")
    return dict(hydrated.payload)


def _codex_audit(stdout: str) -> dict[str, Any]:
    session_id = ""
    usage: dict[str, Any] = {}
    for line in stdout.splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if row.get("type") == "thread.started":
            session_id = str(row.get("thread_id") or "")
        if isinstance(row.get("usage"), Mapping):
            usage = dict(row["usage"])
    return {"provider_session_id": session_id, "usage": usage}


def _claude_result(stdout: str) -> tuple[str, dict[str, Any]]:
    try:
        row = json.loads(stdout)
    except json.JSONDecodeError:
        return stdout, {"provider_session_id": "", "usage": {}}
    if not isinstance(row, Mapping):
        return stdout, {"provider_session_id": "", "usage": {}}
    return str(row.get("result") or ""), {
        "provider_session_id": str(row.get("session_id") or ""),
        "usage": dict(row.get("usage") or {}),
    }


def _payload(event: ZfEvent) -> dict[str, Any]:
    return event.payload if isinstance(event.payload, dict) else {}


__all__ = ["execute_evolution_request"]
