"""Structured autoresearch reflection/replan/deposition artifacts."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from zf.autoresearch.loop_types import IterationRecord
from zf.runtime.sidecar_refs import write_sidecar_json


def _clean_str(value: Any) -> str:
    return str(value or "").strip()


@dataclass(frozen=True)
class ReflectionArtifact:
    schema_version: str = "reflection.v1"
    artifact_id: str = ""
    run_id: str = ""
    iteration: int = 0
    verdict: str = ""
    risk: str = ""
    recommendation: str = ""
    alternatives: list[str] = field(default_factory=list)
    evidence_refs: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ReplanProposal:
    schema_version: str = "replan-proposal.v1"
    artifact_id: str = ""
    run_id: str = ""
    iteration: int = 0
    reason: str = ""
    changed_prompt: str = ""
    scenario_delta: list[str] = field(default_factory=list)
    risk: str = ""
    required_gate: str = "eval-result.v1 gate passed"
    evidence_refs: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CapabilityDeposition:
    schema_version: str = "capability-deposition.v1"
    artifact_id: str = ""
    run_id: str = ""
    iteration: int = 0
    capability: str = ""
    target_asset: str = "autoresearch_scenario"
    trigger: str = ""
    verification: str = ""
    evidence_refs: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _write_json(path: Path, payload: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return path


def write_reflection_artifacts(
    output_dir: Path,
    record: IterationRecord,
    *,
    evidence_refs: list[str],
    state_dir: Path | None = None,
) -> dict[str, Any]:
    """Write structured artifacts implied by an iteration reflection.

    Returns artifact kind -> path. These artifacts are proposals only; they do
    not mutate tasks, skills, config, or runtime truth.
    """
    if record.reflect is None:
        return {}
    root = Path(output_dir) / "artifacts"
    refs = [str(ref) for ref in evidence_refs if str(ref)]
    stem = f"iter-{record.iter:03d}-{record.run_id}"
    reflection = ReflectionArtifact(
        artifact_id=f"reflection-{record.run_id}-{record.iter}",
        run_id=record.run_id,
        iteration=record.iter,
        verdict=record.reflect.verdict,
        risk=record.reflect.risk,
        recommendation=record.reflect.rec_for_next_iter,
        alternatives=list(record.reflect.alternatives),
        evidence_refs=refs,
    )
    out = {
        "reflection": str(_write_json(root / f"{stem}-reflection.json", reflection.to_dict())),
    }
    if (
        record.reflect.rec_for_next_iter.strip()
        and record.reflect.verdict in {"better_fix_exists", "regression", "unknown"}
    ):
        replan = ReplanProposal(
            artifact_id=f"replan-{record.run_id}-{record.iter}",
            run_id=record.run_id,
            iteration=record.iter,
            reason=record.reflect.verdict,
            changed_prompt=record.reflect.rec_for_next_iter,
            scenario_delta=[record.scenario],
            risk=record.reflect.risk,
            evidence_refs=refs + [out["reflection"]],
        )
        out["replan"] = str(_write_json(root / f"{stem}-replan.json", replan.to_dict()))
    if record.run_status in {"passed", "passed_after_rework"} and record.reflect.verdict == "best_so_far":
        deposition = CapabilityDeposition(
            artifact_id=f"deposition-{record.run_id}-{record.iter}",
            run_id=record.run_id,
            iteration=record.iter,
            capability=(
                _clean_str(record.reflect.rec_for_next_iter)
                or f"Scenario {record.scenario} passed with stable evidence"
            ),
            trigger=f"autoresearch loop {record.scenario} passed",
            verification="eval-result.v1 gate passed",
            evidence_refs=refs + [out["reflection"]],
        )
        out["deposition"] = str(_write_json(
            root / f"{stem}-deposition.json",
            deposition.to_dict(),
        ))
    if state_dir is not None:
        sidecar_refs: dict[str, Any] = {}
        for kind, path_text in list(out.items()):
            path = Path(path_text)
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            descriptor = write_sidecar_json(
                state_dir,
                f"diagnostics/autoresearch/{record.run_id}/iter-{record.iter:03d}-{kind}.json",
                payload,
                kind="diagnostic_trace",
                schema_version=str(payload.get("schema_version") or f"autoresearch.{kind}.v1"),
                created_by="autoresearch",
                access_scope={
                    "visibility": "project",
                    "actor": "run-manager",
                    "purpose": f"autoresearch-{kind}",
                },
                retention={"class": "audit_required"},
                required=False,
                preview=_clean_str(payload.get("recommendation") or payload.get("reason") or kind)[:200],
            )
            sidecar_refs[kind] = descriptor
        if sidecar_refs:
            out["sidecar_refs"] = sidecar_refs
    return out


def build_replan_contract_eval_request(
    proposal: ReplanProposal | dict[str, Any],
    *,
    proposal_ref: str,
    trigger_event_id: str,
    feature_id: str,
    candidate_task_map_ref: str,
    old_task_map_ref: str = "",
    expected_current_task_map_ref: str = "",
    profile: str = "baseline",
) -> dict[str, Any]:
    """Build a proposal-only request for runtime replan contract eval.

    Autoresearch proposes; Product Delivery still owns adoption. The request is
    intentionally refs-only so it can be emitted as `replan.contract_eval.requested`
    without inlining plan or task-map bodies.
    """
    data = proposal.to_dict() if isinstance(proposal, ReplanProposal) else dict(proposal)
    artifact_id = _clean_str(data.get("artifact_id"))
    seed = "|".join([
        artifact_id,
        proposal_ref,
        trigger_event_id,
        feature_id,
        candidate_task_map_ref,
        expected_current_task_map_ref,
    ])
    fingerprint = hashlib.sha1(seed.encode("utf-8")).hexdigest()[:16]
    return {
        "schema_version": "replan-contract-eval-request.v1",
        "request_id": f"replan-eval-{fingerprint}",
        "idempotency_key": f"replan-eval:{fingerprint}",
        "proposal_ref": proposal_ref,
        "proposal_id": artifact_id,
        "trigger_event_id": trigger_event_id,
        "feature_id": feature_id,
        "candidate_task_map_ref": candidate_task_map_ref,
        "old_task_map_ref": old_task_map_ref,
        "expected_current_task_map_ref": expected_current_task_map_ref,
        "profile": profile or "baseline",
        "apply_policy": "proposal_only",
        "sandbox_required": True,
        "reason": _clean_str(data.get("reason")),
        "risk": _clean_str(data.get("risk")),
        "evidence_refs": [
            _clean_str(ref)
            for ref in data.get("evidence_refs") or []
            if _clean_str(ref)
        ],
    }


__all__ = [
    "ReflectionArtifact",
    "ReplanProposal",
    "CapabilityDeposition",
    "build_replan_contract_eval_request",
    "write_reflection_artifacts",
]
