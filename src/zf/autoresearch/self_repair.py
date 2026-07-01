"""Deterministic contracts for supervised autoresearch self-repair."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from zf.core.events.model import ZfEvent
from zf.core.state.atomic_io import atomic_write_text


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _stable_id(prefix: str, *parts: str) -> str:
    digest = hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()[:10].upper()
    return f"{prefix}-{digest}"


@dataclass(frozen=True)
class HarnessImprovementCandidate:
    candidate_id: str
    trigger_id: str
    source_event_id: str
    source_event_type: str
    fingerprint: str
    severity: str
    hypothesis: str
    source_signals: list[str] = field(default_factory=list)
    evidence_paths: list[str] = field(default_factory=list)
    target_metrics: list[str] = field(default_factory=list)
    eval_plan: list[str] = field(default_factory=list)
    status: str = "proposed"
    created_at: str = field(default_factory=_now_iso)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RepairMetricValidation:
    passed: bool
    baseline_metrics: dict[str, float] = field(default_factory=dict)
    candidate_metrics: dict[str, float] = field(default_factory=dict)
    deltas: dict[str, float] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def candidate_from_trigger_event(event: ZfEvent) -> HarnessImprovementCandidate:
    payload = event.payload if isinstance(event.payload, dict) else {}
    trigger_id = str(payload.get("trigger_id") or event.id or "").strip()
    fingerprint = str(payload.get("fingerprint") or trigger_id or event.id).strip()
    reason = str(payload.get("reason") or "autoresearch trigger accepted").strip()
    signal_ids = payload.get("signal_ids") if isinstance(payload.get("signal_ids"), list) else []
    evidence_paths = (
        payload.get("evidence_paths")
        if isinstance(payload.get("evidence_paths"), list) else []
    )
    metric_impacts = (
        payload.get("metric_impacts")
        if isinstance(payload.get("metric_impacts"), dict) else {}
    )
    target_metrics = sorted(str(key) for key in metric_impacts) or [
        "failure_signal_resolved",
        "autoresearch_eval_passed",
        "regression_tests_passed",
    ]
    return HarnessImprovementCandidate(
        candidate_id=_stable_id("HIC", trigger_id, fingerprint),
        trigger_id=trigger_id,
        source_event_id=event.id,
        source_event_type=event.type,
        fingerprint=fingerprint,
        severity=str(payload.get("severity") or "high"),
        hypothesis=reason,
        source_signals=[str(item) for item in signal_ids if str(item).strip()],
        evidence_paths=[str(item) for item in evidence_paths if str(item).strip()],
        target_metrics=target_metrics,
        eval_plan=[
            "reproduce the captured failure signal from evidence_paths",
            "run focused autoresearch validation for the candidate fix",
            "run the relevant zf pytest target before leaving maintenance",
        ],
    )


def candidate_artifact_path(state_dir: Path, candidate_id: str) -> Path:
    return (
        Path(state_dir)
        / "autoresearch"
        / "self-repair"
        / "candidates"
        / f"{candidate_id}.json"
    )


def write_candidate_artifact(
    state_dir: Path,
    candidate: HarnessImprovementCandidate,
) -> Path:
    path = candidate_artifact_path(state_dir, candidate.candidate_id)
    atomic_write_text(
        path,
        json.dumps(candidate.to_dict(), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )
    return path


def repair_task_payload_from_candidate(
    candidate: HarnessImprovementCandidate,
    *,
    candidate_path: Path,
) -> dict[str, Any]:
    task_id = _stable_id("TASK-AR", candidate.candidate_id)
    evidence_contract = {
        "source": "autoresearch_self_repair",
        "candidate_id": candidate.candidate_id,
        "trigger_id": candidate.trigger_id,
        "source_event_id": candidate.source_event_id,
        "fingerprint": candidate.fingerprint,
        "candidate_path": str(candidate_path),
        "source_signals": list(candidate.source_signals),
        "evidence_paths": list(candidate.evidence_paths),
        "target_metrics": list(candidate.target_metrics),
        "eval_plan": list(candidate.eval_plan),
        "success_criteria": [
            {
                "kind": "event_exists",
                "event_type": "autoresearch.validation.passed",
                "task_id": task_id,
            },
            {
                "kind": "command_passed",
                "command": "uv run pytest tests/test_autoresearch_triggers.py",
            },
        ],
    }
    return {
        "task_id": task_id,
        "title": f"Autoresearch self-repair: {candidate.hypothesis}",
        "key": f"autoresearch:{candidate.candidate_id}",
        "priority": 1 if candidate.severity in {"critical", "high"} else 2,
        "assigned_to": "dev",
        "skills_required": ["zf-cr"],
        "contract": {
            "schema_version": "task-contract.v1",
            "phase": "zaofu_self_repair",
            "behavior": candidate.hypothesis,
            "verification": "Run focused autoresearch eval and relevant pytest target.",
            "verification_tiers": ["static", "runtime"],
            "scope": ["src/zf/**", "tests/**"],
            "acceptance": "autoresearch validation passes and no regression tests fail",
            "owner_role": "dev",
            "complexity": "complex",
            "evidence_contract": evidence_contract,
            "validation": {
                "release_boundary": True,
                "requires_baseline_candidate_metrics": True,
            },
        },
    }


def validate_repair_metric_delta(
    baseline_metrics: dict[str, Any],
    candidate_metrics: dict[str, Any],
    *,
    required_metrics: list[str] | None = None,
) -> RepairMetricValidation:
    errors: list[str] = []
    required = required_metrics or sorted(set(baseline_metrics) | set(candidate_metrics))
    if not baseline_metrics:
        errors.append("baseline_metrics are required")
    if not candidate_metrics:
        errors.append("candidate_metrics are required")

    normalized_baseline: dict[str, float] = {}
    normalized_candidate: dict[str, float] = {}
    deltas: dict[str, float] = {}
    for metric in required:
        if metric not in baseline_metrics:
            errors.append(f"baseline metric {metric!r} is missing")
            continue
        if metric not in candidate_metrics:
            errors.append(f"candidate metric {metric!r} is missing")
            continue
        try:
            base_value = float(baseline_metrics[metric])
            candidate_value = float(candidate_metrics[metric])
        except (TypeError, ValueError):
            errors.append(f"metric {metric!r} must be numeric")
            continue
        normalized_baseline[metric] = base_value
        normalized_candidate[metric] = candidate_value
        deltas[metric] = candidate_value - base_value

    return RepairMetricValidation(
        passed=not errors,
        baseline_metrics=normalized_baseline,
        candidate_metrics=normalized_candidate,
        deltas=deltas,
        errors=errors,
    )


__all__ = [
    "HarnessImprovementCandidate",
    "RepairMetricValidation",
    "candidate_from_trigger_event",
    "candidate_artifact_path",
    "write_candidate_artifact",
    "repair_task_payload_from_candidate",
    "validate_repair_metric_delta",
]
