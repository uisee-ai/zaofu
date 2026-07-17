"""Mechanical workflow request policy shared by CLI, Web, and project init."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class WorkflowKindPolicy:
    kind: str
    default_lanes: int
    default_tier: str
    required_fields: tuple[str, ...]


WORKFLOW_KIND_POLICIES: dict[str, WorkflowKindPolicy] = {
    "issue": WorkflowKindPolicy(
        kind="issue",
        default_lanes=1,
        default_tier="standard",
        required_fields=("objective",),
    ),
    "prd": WorkflowKindPolicy(
        kind="prd",
        default_lanes=2,
        default_tier="standard",
        required_fields=("objective", "target_root"),
    ),
    "refactor": WorkflowKindPolicy(
        kind="refactor",
        default_lanes=5,
        default_tier="standard",
        required_fields=("objective", "source_root", "target_root"),
    ),
}


def normalize_workflow_kind(kind: str, *, allow_auto: bool = False) -> str:
    value = str(kind or "").strip().lower()
    if value == "feat":
        return "prd"
    if allow_auto and value in {"", "auto"}:
        return "auto"
    if value not in WORKFLOW_KIND_POLICIES:
        raise ValueError(
            "workflow kind must be issue, prd, refactor, feat"
            + (", or auto" if allow_auto else "")
        )
    return value


def workflow_kind_policy(kind: str) -> WorkflowKindPolicy:
    return WORKFLOW_KIND_POLICIES[normalize_workflow_kind(kind)]


def default_lanes_for_kind(kind: str) -> int:
    return workflow_kind_policy(kind).default_lanes


def required_fields_for_kind(kind: str) -> list[str]:
    return list(workflow_kind_policy(kind).required_fields)


def missing_fields_for_kind(
    kind: str,
    *,
    objective: str = "",
    source_ref: str = "",
    source_root: str = "",
    target_root: str = "",
) -> list[str]:
    values = {
        "objective": objective or source_ref,
        "source_root": source_root,
        "target_root": target_root,
    }
    return [
        field
        for field in required_fields_for_kind(kind)
        if not str(values.get(field) or "").strip()
    ]


__all__ = [
    "WORKFLOW_KIND_POLICIES",
    "WorkflowKindPolicy",
    "default_lanes_for_kind",
    "missing_fields_for_kind",
    "normalize_workflow_kind",
    "required_fields_for_kind",
    "workflow_kind_policy",
]
