"""Prepare a fresh idle session after completed reader work."""
from __future__ import annotations

from typing import TYPE_CHECKING

from zf.core.events.model import ZfEvent
from zf.core.task.schema import Task
from zf.runtime.injection import generate_role_instructions
from zf.runtime.recovery import build_recovery_briefing

if TYPE_CHECKING:
    from zf.core.config.schema import RoleConfig
    from zf.runtime.orchestrator import Orchestrator


def prepare_idle_recovery(runtime: Orchestrator, role: RoleConfig) -> Task | None:
    """Return the idle briefing task, or fail without discarding active work."""
    # Reader work may have no canonical Task. Never demote an active
    # fanout to idle or discard its frozen treatment.
    if runtime._active_fanout_child_for_instance(role.instance_id) is not None:
        return None
    runtime._clear_role_skill_treatment(role.instance_id)
    # Idle instances should come back as clean workers, not as a
    # resumed copy of their previous task. Refresh role instructions
    # without a Current Task block. Context recycle skips prompt
    # injection; watchdog respawn still injects a compact recovery note
    # so recovery delivery failures remain visible.
    try:
        idle_task = Task(
            id="(idle)",
            title="(idle)",
            status="in_progress",
            assigned_to=role.instance_id,
        )
        briefing_md = build_recovery_briefing(
            runtime.state_dir,
            role.instance_id,
            idle_task,
            git_context=None,
            compact=True,
            config=runtime.config,
            project_root=runtime.project_root,
        )
        briefing_dir = runtime.state_dir / "briefings"
        briefing_dir.mkdir(parents=True, exist_ok=True)
        (briefing_dir / f"{role.instance_id}-recovery.md").write_text(
            briefing_md,
            encoding="utf-8",
        )
        # ZF-CONTEXT-REC-001 integration (2026-05-18): write
        # State Packet–driven recovery briefing alongside the
        # legacy one. Best-effort — recovery success uses the
        # legacy path; SP-based version is for operator /
        # downstream tooling.
        runtime._write_state_packet_recovery_briefing(
            role=role,
            task_id=getattr(idle_task, "id", None),
            recovery_reason="idle_after_recycle",
        )
        skill_entries = runtime._record_skill_provenance(
            role=role,
            task_id=None,
        )
        instructions_dir = runtime.state_dir / "instructions"
        instructions_dir.mkdir(parents=True, exist_ok=True)
        (instructions_dir / f"{role.instance_id}.md").write_text(
            generate_role_instructions(
                runtime.config,
                role,
                task=None,
                skill_entries=skill_entries,
                state_dir_ref=runtime.state_dir,
                project_root=getattr(runtime, "project_root", None),
            ),
            encoding="utf-8",
        )
    except Exception as exc:
        runtime.event_writer.append(ZfEvent(
            type="worker.recovery.skipped",
            actor=role.instance_id,
            payload={
                "role": role.name,
                "reason": "idle_recovery_preparation_failed",
                "error_type": type(exc).__name__,
                "error": str(exc)[:500],
            },
        ))
        return None
    return idle_task
