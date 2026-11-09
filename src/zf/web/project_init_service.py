"""Web adapter for the shared Project Init implementation."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from zf.core.workspace.registry import WorkspaceRegistry
from zf.web.project_init_policy import ProjectInitConfigDraft
from zf.web.projections.workspace import _workspace_project_payload


def apply_project_profile_overlay(
    root: Path,
    *,
    stack: str = "",
    surface: str = "",
    scale: str = "",
    scaffold: bool = False,
    intent: str = "build",
) -> dict[str, Any]:
    """Apply the post-init stack profile without overwriting project choices."""
    from zf.core.profile.apply import (
        apply_agents_md_stack,
        fill_required_checks,
        scaffold_from_zero,
    )
    from zf.core.profile.detector import declared_profile, detect
    from zf.core.profile.recommender import recommend

    profile = declared_profile(stack, surface) if stack else detect(root)
    recommendation = recommend(
        profile,
        intent,
        declared=bool(stack),
        scale=scale or None,
    )
    result: dict[str, Any] = {
        "archetype": recommendation.archetype,
        "harness_profile": recommendation.harness_profile,
        "languages": list(profile.languages),
    }
    zf_yaml = root / "zf.yaml"
    if zf_yaml.exists():
        result["required_checks"] = fill_required_checks(
            zf_yaml,
            recommendation.required_checks,
            write=True,
        )
    agents = root / "AGENTS.md"
    if agents.exists():
        result["agents_md"] = apply_agents_md_stack(
            agents,
            profile,
            write=True,
        )["action"]
    if scaffold:
        result["scaffold"] = scaffold_from_zero(
            root,
            profile,
            write=True,
        )["created"]
    return result


def initialize_admitted_project(
    *,
    payload: Mapping[str, Any],
    root: Path,
    generated_config: ProjectInitConfigDraft,
    admission_inspection: Mapping[str, Any],
) -> tuple[dict[str, Any], int]:
    """Initialize an implicit Project through the canonical Python entrypoint."""

    workspace = str(payload.get("workspace") or "default")
    root_exists = bool(admission_inspection.get("root_exists"))
    root_empty = _directory_is_empty(root) if root_exists else False
    greenfield = not root_exists or root_empty
    try:
        from zf.cli.project import init_flow_project

        initialized = init_flow_project(
            kind=generated_config.flow_kind,
            controller_id=generated_config.controller_id,
            name=generated_config.project_name,
            description=generated_config.project_description,
            project_root=root,
            backend=generated_config.primary_backend,
            verify_backend=generated_config.verify_backend,
            stack=str(payload.get("stack") or ""),
            surface=str(payload.get("surface") or ""),
            lanes=generated_config.lanes,
            state_dir=generated_config.state_dir,
            strictness=generated_config.strictness,
            parity_scope=generated_config.parity_scope,
            workspace=workspace,
            force=bool(payload.get("force")),
            create_root=greenfield,
            git_init=greenfield,
            workspace_register=True,
            with_instruction_docs=not bool(payload.get("skip_instruction_docs")),
            notes=str(payload.get("notes") or ""),
        )
    except Exception as exc:
        return {
            "ok": False,
            "status": "init_failed",
            "reason": str(exc),
        }, 422

    project_id = str(initialized.get("workspace_project_id") or "")
    registered_project = (
        WorkspaceRegistry(workspace=workspace).get(project_id)
        if project_id
        else None
    )
    return {
        "ok": True,
        "status": "initialized",
        "state_dir": initialized["state_dir"],
        "instruction_docs": initialized["instruction_docs"],
        "git_hook": initialized.get("git_hook") or "",
        "setup_suggestion": initialized.get("setup_suggestion"),
        "profile": None,
        "notes": initialized.get("notes"),
        "kind": initialized["kind"],
        "project_metadata": initialized["project_metadata"],
        "provider_policy": initialized["provider_policy"],
        "git_readiness": initialized["git_readiness"],
        "config_generated": "registered_controller" if generated_config.controller_id else "typed_flow_spec",
        "controller_id": generated_config.controller_id,
        "project": (
            _workspace_project_payload(registered_project)
            if registered_project is not None
            else None
        ),
    }, 201


def _directory_is_empty(root: Path) -> bool:
    if not root.is_dir():
        return False
    try:
        return not any(root.iterdir())
    except OSError:
        return False


__all__ = ["apply_project_profile_overlay", "initialize_admitted_project"]
