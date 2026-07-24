"""Attempt-domain identity and legacy-compatible feedback selection."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


ATTEMPT_DOMAINS = frozenset({"plan", "task", "candidate", "gap", "recovery"})


def infer_attempt_domain(
    payload: Mapping[str, Any] | None = None,
    *,
    operation_type: str = "",
    stage_id: str = "",
    event_type: str = "",
) -> str:
    raw = payload or {}
    explicit = str(raw.get("attempt_domain") or "").strip().lower()
    if explicit in ATTEMPT_DOMAINS:
        return explicit
    haystack = " ".join((
        operation_type,
        stage_id,
        event_type,
        str(raw.get("operation_kind") or ""),
        str(raw.get("failure_scope") or ""),
    )).lower()
    if any(marker in haystack for marker in ("recovery", "run-manager", "autoresearch")):
        return "recovery"
    if any(marker in haystack for marker in ("gap", "replan")):
        return "gap"
    if any(marker in haystack for marker in ("candidate", "assembly", "integration")):
        return "candidate"
    if any(marker in haystack for marker in ("plan", "scan", "critic", "research")):
        return "plan"
    return "task"


def attempt_identity_fields(
    payload: Mapping[str, Any],
    *,
    operation_type: str = "",
    stage_id: str = "",
    event_type: str = "",
) -> dict[str, str]:
    return {
        key: value
        for key, value in {
            "attempt_domain": infer_attempt_domain(
                payload,
                operation_type=operation_type,
                stage_id=stage_id,
                event_type=event_type,
            ),
            "task_id": str(payload.get("task_id") or ""),
            "task_map_generation": str(payload.get("task_map_generation") or ""),
            "plan_artifact_package_id": str(
                payload.get("plan_artifact_package_id") or ""
            ),
            "plan_artifact_package_digest": str(
                payload.get("plan_artifact_package_digest") or ""
            ),
        }.items()
        if value
    }


def feedback_matches_attempt(
    feedback: Mapping[str, Any],
    target: Mapping[str, Any],
) -> bool:
    """Reject cross-domain/currentness feedback while accepting legacy blanks."""

    for key in (
        "attempt_domain",
        "task_id",
        "task_map_generation",
        "plan_artifact_package_id",
        "plan_artifact_package_digest",
    ):
        actual = str(feedback.get(key) or "")
        expected = str(target.get(key) or "")
        if actual and expected and actual != expected:
            return False
    return True


__all__ = [
    "ATTEMPT_DOMAINS",
    "attempt_identity_fields",
    "feedback_matches_attempt",
    "infer_attempt_domain",
]
