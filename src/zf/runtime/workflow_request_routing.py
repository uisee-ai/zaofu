"""Mechanical bindings for unresolved intake and repeated semantic requests."""

from pathlib import Path
from typing import Any

from zf.runtime.workflow_requests import hydrate_workflow_requirement


def request_revision_needed(state_dir: Path, request: dict[str, Any], payload: dict) -> bool:
    if not request.get("confirmed"):
        return True
    spec = hydrate_workflow_requirement(state_dir, request)
    for key in ("kind", "objective", "source_root", "target_root", "acceptance", "constraints", "open_questions"):
        value = payload.get(key)
        if key == "objective":
            value = payload.get("objective") or payload.get("message") or payload.get("title")
            if value is not None:
                # Intake compacts whitespace before pinning the Requirement.
                if " ".join(str(value).split()) == " ".join(str(spec.get(key) or "").split()):
                    continue
        elif key == "target_root":
            value = payload.get("target_root") or payload.get("target")
        elif key == "kind":
            if value in (None, "", "auto"):
                continue
            value = "prd" if value == "feat" else value
        if value is not None and value != spec.get(key):
            return True
    return False


def unresolved_route_blocker() -> dict[str, Any]:
    return {
        "severity": "STOP", "kind": "workflow_route_selection_required",
        "title": "需要语义选路或显式选择",
        "message": "auto 仅收集需求，关键词候选不能确定可执行路线。",
        "fix_it": "显式选择有效 kind/route，或提供 synthesis_backend 进行语义建议；建议仍需准入。",
        "safe_auto_fix": False,
    }


def synthesis_request_is_ready(request: dict[str, Any]) -> bool:
    """Permit initial selection or reuse of a current, bound semantic result."""
    return request.get("status") == "ready" or bool(
        request.get("status") == "proposed"
        and request.get("synthesis_ref")
        and request.get("synthesis_revision") == request.get("revision")
    )
