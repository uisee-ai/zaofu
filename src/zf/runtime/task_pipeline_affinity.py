"""Task-stage provider-session affinity for the v4 Task Pipeline."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from zf.core.state.role_sessions import RoleSessionRegistry


def task_stage_spawn_cwd(runtime: Any, role_instance: str) -> Path | None:
    """Keep lifecycle spawns in the workspace of the activated Task-stage."""
    registry = RoleSessionRegistry(
        Path(runtime.state_dir) / "role_sessions.yaml",
        project_root=str(runtime.project_root),
    )
    key = registry.instance_meta().get(role_instance, {}).get("active_task_stage_binding_key")
    if not key:
        return None
    binding = registry.task_stage_bindings().get(str(key))
    if not binding or binding.get("status") != "active" or (
        binding.get("current_role_instance") != role_instance
    ):
        raise RuntimeError(f"Task-stage spawn binding is not current: {role_instance}")
    identity = {
        "task_stage_session_binding": key,
        "role_instance": role_instance,
        "workflow_run_id": binding["workflow_run_id"],
        "task_id": binding["task_id"],
        "task_pipeline_stage": binding["stage"],
        "workspace_generation": binding["workspace_generation"],
        "placement_epoch": binding["current_placement_epoch"],
    }
    dispatch = next((event.payload for event in reversed(runtime.event_log.read_all())
                     if event.type == "task.pipeline.stage.dispatched"
                     and event.origin == "kernel"
                     and all(event.payload.get(k) == v for k, v in identity.items())), None)
    if dispatch is None:
        raise RuntimeError(f"Task-stage spawn placement is unproven: {role_instance}")
    from zf.core.safety import PathGuard
    from zf.runtime.task_workspaces import TaskWorkspaceManager

    manager = TaskWorkspaceManager(
        state_dir=Path(runtime.state_dir), project_root=Path(runtime.project_root),
        config=runtime.config,
    )
    plan = manager.plan(
        workflow_run_id=binding["workflow_run_id"], task_id=binding["task_id"],
        task_map_generation=dispatch["task_map_generation"],
        workspace_generation=binding["workspace_generation"],
        base_ref=dispatch["base_commit"],
    )
    project_path = Path(plan.project_path)
    PathGuard.assert_under(project_path, Path(runtime.state_dir))
    if (not plan.enabled or plan.mode != "worktree" or not project_path.is_dir()
            or project_path.resolve() != project_path
            or str(project_path) != dispatch.get("workdir")):
        raise RuntimeError(f"Task-stage spawn workspace is missing or mismatched: {role_instance}")
    manager._assert_owned(Path(plan.workdir))
    manager._assert_metadata_current(plan)
    return project_path


def task_pipeline_preferred_roles(
    runtime: Any,
    *,
    generation_contexts: Mapping[str, Mapping[str, Any]],
) -> dict[str, dict[str, str]]:
    """Prefer the slot that owns a Task-stage provider transcript."""

    if not hasattr(runtime, "state_dir") or not hasattr(runtime, "project_root"):
        return {}
    registry = RoleSessionRegistry(
        Path(runtime.state_dir) / "role_sessions.yaml",
        project_root=str(runtime.project_root),
    )
    configured_instances = {
        str(role.instance_id)
        for role in getattr(runtime.config, "roles", []) or []
        if str(role.instance_id).strip()
    }
    result: dict[str, dict[str, str]] = {}
    for binding in registry.task_stage_bindings().values():
        task_id = str(binding.get("task_id") or "")
        context = generation_contexts.get(task_id)
        if context is None or str(binding.get("status") or "") == "archived":
            continue
        task_map_generation = str(context.get("task_map_generation") or "")
        if not str(binding.get("rework_affinity_id") or "").startswith(
            f"{task_map_generation}:"
        ):
            continue
        stage = str(binding.get("stage") or "")
        preferred = str(
            binding.get("provider_session_role_instance") or ""
        )
        if not preferred:
            preferred = role_instance_from_codex_session_path(
                Path(runtime.state_dir),
                str(binding.get("session_path") or ""),
            )
        preferred = preferred or str(
            binding.get("current_role_instance") or ""
        )
        if stage and preferred in configured_instances:
            result.setdefault(task_id, {})[stage] = preferred
    return result


def role_instance_from_codex_session_path(
    state_dir: Path,
    session_path: str,
) -> str:
    if not session_path:
        return ""
    try:
        relative = Path(session_path).resolve().relative_to(
            (Path(state_dir) / "workdirs").resolve()
        )
    except (OSError, ValueError):
        return ""
    parts = relative.parts
    if len(parts) < 3 or parts[1:3] != ("codex-home", "sessions"):
        return ""
    return str(parts[0])


__all__ = [
    "role_instance_from_codex_session_path",
    "task_pipeline_preferred_roles",
]
