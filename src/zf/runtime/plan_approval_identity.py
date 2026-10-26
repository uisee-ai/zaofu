"""Bind a reviewed artifact fallback into pending Plan approval identity."""

from __future__ import annotations

from typing import Any

from zf.runtime.plan_approval_fingerprint import plan_approval_fingerprint as fingerprint


def plan_approval_fingerprint(
    *,
    stage_id: str,
    pdd_id: str,
    task_items: list[Any],
    artifact_digest: str = "",
    identity: dict[str, str] | None = None,
) -> str:
    bound_identity = dict(identity or {})
    if artifact_digest and not bound_identity.get("plan_artifact_package_digest"):
        bound_identity["plan_artifact_package_digest"] = artifact_digest
    return fingerprint(
        stage_id=stage_id, pdd_id=pdd_id, task_items=task_items,
        identity=bound_identity,
    )
