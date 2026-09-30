"""Lifecycle spawn cwd: Task-stage placement and the existing feature role workdir."""

from dataclasses import asdict
from pathlib import Path
from typing import Any

from zf.core.config.schema import RoleConfig
from zf.core.events.model import ZfEvent


def role_spawn_cwd(runtime: Any, role: RoleConfig, *, source: str) -> Path | None:
    """Resolve cwd for lifecycle-managed spawns.

    Initial `zf start` launches workers from their configured worktree
    project paths. Respawn/recycle must preserve that invariant; otherwise
    a recovered writer silently falls back to the harness root.
    """
    from zf.runtime.task_pipeline_affinity import task_stage_spawn_cwd

    task_cwd = task_stage_spawn_cwd(runtime, role.instance_id)
    if task_cwd is not None:
        return task_cwd
    workdirs = getattr(getattr(runtime.config, "runtime", None), "workdirs", None)
    if workdirs is None or not getattr(workdirs, "enabled", False):
        return None

    from zf.runtime.workdirs import WorkdirManager

    manager = WorkdirManager(
        state_dir=runtime.state_dir,
        project_root=runtime.project_root,
        config=runtime.config,
    )
    plan = manager.prepare(role)
    try:
        payload = asdict(plan)
        payload["source"] = source
        runtime.event_writer.append(ZfEvent(
            type="workdir.prepared",
            actor="zf-cli",
            payload=payload,
        ))
    except Exception:
        pass

    project_path = Path(plan.project_path)
    if (
        plan.enabled
        and plan.mode == "worktree"
        and plan.role_kind in {"writer", "reader"}
    ):
        if not project_path.exists():
            raise RuntimeError(
                f"workdir project path missing for {role.instance_id}: "
                f"{project_path}"
            )
        return project_path
    return None
