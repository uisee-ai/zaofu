"""Pure policy for drafting a newly admitted Project configuration."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from zf.cli.flow import draft_flow_spec, draft_multi_kind_project_spec
from zf.cli.project_controller import DEFAULT_PROJECT_CONTROLLER, draft_project_controller
from zf.core.config.backend_identity import canonical_backend_id
from zf.core.workflow.request_policy import default_lanes_for_kind
from zf.core.workspace.onboarding import (
    normalize_primary_backend,
    read_onboarding,
    secondary_backend_for,
)
from zf.core.workspace.project_admission import normalize_new_project_metadata


@dataclass(frozen=True)
class ProjectInitConfigDraft:
    flow_kind: str
    project_name: str
    project_description: str
    primary_backend: str
    verify_backend: str
    lanes: int
    state_dir: str
    strictness: str
    parity_scope: tuple[str, ...]
    documents: list[dict[str, Any]]
    controller_id: str = ""

    @property
    def mixed_enabled(self) -> bool:
        return bool(self.verify_backend)


def draft_project_init_config(
    *,
    payload: Mapping[str, Any],
    root: Path,
    flow_kind: str,
    inherit_onboarding: bool,
) -> ProjectInitConfigDraft:
    """Validate admission inputs and produce typed config documents."""

    raw_name = payload.get("name")
    if raw_name is None or raw_name == "":
        raw_name = payload.get("project_name", "")
    project_name, project_description = normalize_new_project_metadata(
        root=root,
        name=raw_name,
        description=payload.get("description", ""),
    )
    backend = str(payload.get("backend") or "")
    mixed_enabled = bool(payload.get("mixed_enabled", False))
    verify_backend = str(payload.get("verify_backend") or "")

    if inherit_onboarding:
        onboarding = read_onboarding()
        backend = normalize_primary_backend(
            backend or onboarding.backend,
            default="codex",
        )
        mixed_enabled = (
            bool(payload.get("mixed_enabled"))
            if "mixed_enabled" in payload
            else onboarding.mixed_enabled
        )
        verify_backend = secondary_backend_for(backend) if mixed_enabled else ""
    elif mixed_enabled and not verify_backend:
        backend = normalize_primary_backend(backend, default="codex")
        verify_backend = secondary_backend_for(backend)

    backend = canonical_backend_id(backend or "codex")
    verify_backend = canonical_backend_id(verify_backend)
    if "mixed" in {backend, verify_backend}:
        raise ValueError(
            "mixed is a provider policy, not a runtime backend; "
            "use mixed_enabled with a primary backend"
        )
    if verify_backend == backend:
        verify_backend = ""

    lanes_raw = payload.get("lanes") or payload.get("requested_lanes")
    try:
        lanes = int(lanes_raw) if lanes_raw else 0
    except (TypeError, ValueError) as exc:
        raise ValueError("lanes must be an integer") from exc
    controller_id = DEFAULT_PROJECT_CONTROLLER if inherit_onboarding and flow_kind == "prd" else ""
    if not lanes and flow_kind != "multi" and not controller_id:
        lanes = default_lanes_for_kind(flow_kind)

    parity_scope_raw = payload.get("parity_scope") or payload.get("parityScope") or []
    if isinstance(parity_scope_raw, str):
        parity_scope = tuple(
            item.strip() for item in parity_scope_raw.split(",") if item.strip()
        )
    elif isinstance(parity_scope_raw, list):
        parity_scope = tuple(
            str(item).strip()
            for item in parity_scope_raw
            if str(item).strip()
        )
    else:
        parity_scope = ()

    common = {
        "backend": backend,
        "verify_backend": verify_backend,
        "lanes": lanes,
        "project_name": project_name,
        "project_description": project_description,
        "state_dir": str(payload.get("state_dir") or ""),
        "project_root": root,
        "strictness": str(payload.get("strictness") or "standard"),
        "parity_scope": parity_scope,
    }
    if controller_id:
        documents = draft_project_controller(
            controller_id=controller_id, project_root=root, project_name=project_name,
            project_description=project_description, backend=backend, verify_backend=verify_backend,
            state_dir=common["state_dir"], lanes=lanes,
            stack=str(payload.get("stack") or ""), surface=str(payload.get("surface") or ""),
        )
    elif flow_kind == "multi":
        documents = draft_multi_kind_project_spec(**common)
    else:
        documents = draft_flow_spec(
            kind=flow_kind,
            source_ref=str(
                payload.get("from")
                or payload.get("source_ref")
                or payload.get("objective_ref")
                or ""
            ),
            source_root=str(
                payload.get("source_root") or payload.get("sourceRoot") or ""
            ),
            target_root=str(
                payload.get("target_root")
                or payload.get("targetRoot")
                or payload.get("target")
                or ""
            ),
            **common,
        )
    return ProjectInitConfigDraft(
        flow_kind=flow_kind,
        project_name=project_name,
        project_description=project_description,
        primary_backend=backend,
        verify_backend=verify_backend,
        lanes=lanes,
        state_dir=common["state_dir"],
        strictness=common["strictness"],
        parity_scope=parity_scope,
        documents=documents,
        controller_id=controller_id,
    )


__all__ = ["ProjectInitConfigDraft", "draft_project_init_config"]
