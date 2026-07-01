"""Deterministic profile and gate policy projection.

This module turns project profile hints into a small, testable policy object.
It does not dispatch workers or mutate task truth.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from zf.core.config.schema import ZfConfig
from zf.core.task.schema import Task
from zf.runtime.fast_path import evaluate_fast_path
from zf.runtime.long_horizon import (
    complexity_strict_reasons_for_task,
    effective_profile_for_task,
)


_FULL_STAGES = (
    "design",
    "design_critique",
    "implement",
    "static_gate",
    "review",
    "test",
    "judge",
)
_BASELINE_REQUIRED = ("implement", "static_gate", "test")
_BASELINE_OPTIONAL = ("review",)
_BASELINE_SKIPPED = ("design", "design_critique", "judge")


@dataclass(frozen=True)
class GatePolicy:
    profile: str
    effective_profile: str
    required_stages: list[str] = field(default_factory=list)
    optional_stages: list[str] = field(default_factory=list)
    skipped_stages: list[str] = field(default_factory=list)
    promotion_reasons: list[str] = field(default_factory=list)
    required_evidence: dict[str, list[str]] = field(default_factory=dict)
    release_boundary: bool = False
    audit_required: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def gate_policy_for_task(
    task: Task,
    *,
    config: ZfConfig,
    context_usage_ratio: float | None = None,
    text: str = "",
) -> GatePolicy:
    """Return the effective gate policy for one task.

    Baseline keeps runtime evidence hard gates while making LLM-heavy gates
    optional/skippable. Strict/release keep the full standard stage set.
    """

    project_profile = getattr(config.workflow, "harness_profile", "baseline") or "baseline"
    boundary = _boundary_from_task(task)
    effective = effective_profile_for_task(
        task,
        config=config,
        boundary=boundary or "worker_task",
        context_usage_ratio=context_usage_ratio,
    )
    promotion_reasons = complexity_strict_reasons_for_task(
        task,
        config=config,
        context_usage_ratio=context_usage_ratio,
    )
    required = list(_FULL_STAGES)
    optional: list[str] = []
    skipped: list[str] = []
    release_boundary = effective == "release" or boundary in {"release", "ship"}

    if effective == "baseline":
        required = list(_BASELINE_REQUIRED)
        optional = list(_BASELINE_OPTIONAL)
        skipped = list(_BASELINE_SKIPPED)

    fast_path = evaluate_fast_path(
        config.workflow.fast_path,
        scope=_task_scope(task),
        text=text or task.title,
    )
    if fast_path.allowed and effective != "release":
        for stage in config.workflow.fast_path.skip_stages:
            if stage not in skipped:
                skipped.append(stage)
            if stage in required:
                required.remove(stage)
            if stage in optional:
                optional.remove(stage)
        promotion_reasons.append("fast_path:" + "; ".join(fast_path.reasons))

    return GatePolicy(
        profile=project_profile,
        effective_profile=effective,
        required_stages=required,
        optional_stages=optional,
        skipped_stages=skipped,
        promotion_reasons=list(dict.fromkeys(promotion_reasons)),
        required_evidence=_required_evidence_for(required),
        release_boundary=release_boundary,
        audit_required=bool(skipped),
    )


def render_gate_policy(policy: GatePolicy) -> str:
    lines = [
        "## Workflow Gate Policy",
        f"- profile: `{policy.profile}`",
        f"- effective_profile: `{policy.effective_profile}`",
        "- required_stages: " + ", ".join(policy.required_stages),
    ]
    if policy.optional_stages:
        lines.append("- optional_stages: " + ", ".join(policy.optional_stages))
    if policy.skipped_stages:
        lines.append(
            "- skipped_stages: "
            + ", ".join(policy.skipped_stages)
            + " (requires `workflow.stage.skipped` audit evidence)"
        )
    if policy.promotion_reasons:
        lines.append("- reasons: " + "; ".join(policy.promotion_reasons))
    return "\n".join(lines)


def _task_scope(task: Task) -> list[str]:
    contract = task.contract
    return list(dict.fromkeys([
        *contract.scope,
        *contract.affected_files,
        *contract.shared_files,
        *contract.exclusive_files,
        *contract.handoff_artifacts,
    ]))


def _boundary_from_task(task: Task) -> str:
    evidence = task.contract.evidence_contract or {}
    if isinstance(evidence, dict):
        boundary = str(evidence.get("boundary") or evidence.get("profile_boundary") or "")
        if boundary:
            return boundary
    validation = task.contract.validation or {}
    if isinstance(validation, dict):
        return str(validation.get("boundary") or "")
    return ""


def _required_evidence_for(stages: list[str]) -> dict[str, list[str]]:
    evidence: dict[str, list[str]] = {}
    if "static_gate" in stages:
        evidence.setdefault("events", []).append("static_gate.passed")
    if "review" in stages:
        evidence.setdefault("events", []).append("review.approved")
    if "test" in stages:
        evidence.setdefault("events", []).append("test.passed")
    if "judge" in stages:
        evidence.setdefault("events", []).append("judge.passed")
    return evidence


__all__ = [
    "GatePolicy",
    "gate_policy_for_task",
    "render_gate_policy",
]
