"""Flow-kind scoped configuration metadata helpers.

Single-kind configs retain ``workflow.flow_metadata``. Multi-kind canonical
configs store policies under ``workflow.flow_metadata_by_kind`` so one Flow
cannot overwrite another during envelope composition.
"""

from __future__ import annotations

from typing import Any, Mapping


def normalize_flow_kind(value: object) -> str:
    kind = str(value or "").strip().lower()
    return "prd" if kind == "feat" else kind


def flow_kind_from_payload(payload: Mapping[str, Any] | None) -> str:
    body = payload or {}
    return normalize_flow_kind(
        body.get("flow_kind")
        or body.get("request_kind")
        or body.get("goal_kind")
        or body.get("kind")
    )


def flow_metadata_for(
    config: Any,
    flow_kind: str = "",
    *,
    payload: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    workflow = getattr(config, "workflow", None)
    scoped = getattr(workflow, "flow_metadata_by_kind", {}) or {}
    kind = normalize_flow_kind(flow_kind) or flow_kind_from_payload(payload)
    if isinstance(scoped, Mapping):
        candidate = scoped.get(kind)
        if isinstance(candidate, Mapping):
            return dict(candidate)
        if not kind and len(scoped) == 1:
            only = next(iter(scoped.values()))
            if isinstance(only, Mapping):
                return dict(only)
    legacy = getattr(workflow, "flow_metadata", {}) or {}
    return dict(legacy) if isinstance(legacy, Mapping) else {}


__all__ = ["flow_kind_from_payload", "flow_metadata_for", "normalize_flow_kind"]
