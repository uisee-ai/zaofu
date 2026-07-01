"""Uniform recovery briefing assembler.

Used by SpawnCoordinator (on watchdog-triggered respawn) and by the
Sprint E context-recycle flow. Composes a single markdown document
that carries enough state for a fresh CLI invocation to resume work
without losing continuity.

Sections (in order):

    1. Shared memory (last_days=7, or 3 in compact mode)
    2. Per-role memory (same)
    3. Current task contract + briefing
    4. Recent events for this role/task (20, or 10 in compact mode)
    5. Git state
    6. Currently Active (from progress.md `## Currently Active` section)
    7. Role Instructions (from .zf/instructions/<role>.md, if present)
    8. Causal Chain (get_causation_chain for the current task)

G-MEM-4 already made this use ``MemoryStore.get(role, last_days=7)``.
G-RESUME-5 adds sections 6–8 and the ``compact`` mode.
"""

from __future__ import annotations

import json
from pathlib import Path

from zf.core.config.schema import ZfConfig
from zf.core.events.log import EventLog
from zf.core.memory.staleness import StalenessChecker
from zf.core.memory.store import MemoryEntry, MemoryStore
from zf.core.state.git_state import GitState
from zf.core.task.schema import Task
from zf.runtime.git_capture import GitDiffContext, render_git_diff_context


_CURRENT_ACTIVE_HEADER = "## Currently Active"


def build_recovery_briefing(
    state_dir: Path,
    role: str,
    task: Task,
    *,
    git_state: GitState | None = None,
    git_context: GitDiffContext | None = None,
    recent_events_limit: int = 20,
    compact: bool = False,
    config: ZfConfig | None = None,
    project_root: Path | None = None,
) -> str:
    """Assemble the recovery markdown. ``compact=True`` trims memory to
    3 days, events to 10, progress.md to 50 lines, and instructions to
    200 lines — useful for Sprint E recycle where we want to minimize
    the context footprint of the briefing itself."""
    if compact:
        memory_days = 3
        events_limit = 10
        progress_max_lines = 50
        instructions_max_lines = 200
        causation_max_links = 5
    else:
        memory_days = 7
        events_limit = recent_events_limit
        progress_max_lines = 200
        instructions_max_lines = 1000
        causation_max_links = 20

    sections: list[str] = []
    sections.append(f"# Recovery briefing for role `{role}` — task {task.id}")
    sections.append("")
    sections.append("## Recovery Read Order")
    sections.append(_render_recovery_read_order(state_dir, task))
    sections.append("")

    # Section 1: shared memory
    sections.append("## Shared Memory")
    sections.append(_render_memory(state_dir, role=None, last_days=memory_days))
    sections.append("")

    # Section 2: per-role memory
    sections.append(f"## Role Memory ({role})")
    sections.append(_render_memory(state_dir, role=role, last_days=memory_days))
    sections.append("")

    # Section 3: current task
    sections.append("## Current Task")
    sections.extend(_render_task(task))
    sections.append("")

    sections.append("## Runtime Resume Packet")
    sections.append(_render_resume_packet(
        state_dir,
        task,
        role,
        config=config,
        project_root=project_root,
    ))
    sections.append("")

    # Section 4: recent events for this role/task
    sections.append("## Recent Events")
    sections.append(_render_events(state_dir, role, task, events_limit))
    sections.append("")

    # Section 5: git state
    sections.append("## Git State")
    sections.append(_render_git(git_state, git_context))
    sections.append("")

    # Section 6: progress.md Currently Active extract (G-RESUME-5)
    sections.append("## Currently Active (from progress.md)")
    sections.append(_render_progress_active(state_dir, progress_max_lines))
    sections.append("")

    # Section 7: role instructions (G-RESUME-5)
    sections.append("## Role Instructions")
    sections.append(_render_instructions(state_dir, role, instructions_max_lines))
    sections.append("")

    # Section 8: causal chain (G-RESUME-5)
    sections.append("## Causal Chain")
    sections.append(_render_causation_chain(state_dir, task, causation_max_links))
    sections.append("")

    return "\n".join(sections)


def _render_memory(
    state_dir: Path, role: str | None, *, last_days: int = 7,
) -> str:
    memory_dir = state_dir / "memory"
    if not memory_dir.exists():
        return "_(no memory)_"
    store = MemoryStore(memory_dir)
    entries = store.get(role, last_days=last_days)
    if not entries:
        return "_(no entries)_"
    fresh = _filter_stale(entries, state_dir)
    if not fresh:
        return "_(all entries stale)_"
    lines: list[str] = []
    for e in fresh:
        lines.append(f"- **[{e.type}]** {e.content}")
    return "\n".join(lines)


def _filter_stale(entries: list[MemoryEntry], state_dir: Path) -> list[MemoryEntry]:
    # Legacy recovery briefing helper only receives state_dir. Runtime
    # orchestrator paths pass explicit project_root elsewhere; this fallback
    # preserves old tests and default `.zf` projects.
    workspace = state_dir.parent
    checker = StalenessChecker(workspace)
    stale_ids = {id(s.entry) for s in checker.check(entries)}
    return [e for e in entries if id(e) not in stale_ids]


def _render_task(task: Task) -> list[str]:
    lines = [
        f"- **ID**: {task.id}",
        f"- **Title**: {task.title}",
        f"- **Status**: {task.status}",
        f"- **Assigned to**: {task.assigned_to or '_(unassigned)_'}",
    ]
    if task.contract:
        if task.contract.behavior:
            lines.append(f"- **Behavior**: {task.contract.behavior}")
        if task.contract.verification:
            lines.append(f"- **Verification**: `{task.contract.verification}`")
        if task.contract.scope:
            lines.append(f"- **Scope**: {', '.join(task.contract.scope)}")
        if task.contract.exclusions:
            lines.append(f"- **Exclusions**: {', '.join(task.contract.exclusions)}")
        if task.contract.spec_ref:
            lines.append(f"- **spec_ref**: `{task.contract.spec_ref}`")
        if task.contract.plan_ref:
            lines.append(f"- **plan_ref**: `{task.contract.plan_ref}`")
        if task.contract.tdd_ref:
            lines.append(f"- **tdd_ref**: `{task.contract.tdd_ref}`")
        if task.contract.critic_gate_ref:
            lines.append(f"- **critic_gate_ref**: `{task.contract.critic_gate_ref}`")
        if task.contract.acceptance:
            lines.append(f"- **Acceptance**: `{task.contract.acceptance}`")
        if task.contract.acceptance_criteria:
            lines.append("- **Acceptance criteria**:")
            for index, item in enumerate(task.contract.acceptance_criteria, start=1):
                lines.append(f"  {index}. {item}")
        if task.contract.evidence_contract:
            lines.append("- **Evidence contract**:")
            lines.append("```json")
            lines.append(
                json.dumps(
                    task.contract.evidence_contract,
                    ensure_ascii=False,
                    sort_keys=True,
                    indent=2,
                )
            )
            lines.append("```")
    return lines


def _render_recovery_read_order(state_dir: Path, task: Task) -> str:
    try:
        from zf.runtime.task_doc import write_task_doc

        result = write_task_doc(
            state_dir,
            task,
            dispatch_id=task.active_dispatch_id or "recovery",
            source_event="recovery_briefing",
        )
        lines = [
            "1. Re-read the kernel-managed task document before continuing:",
            f"   - task_doc: `{result.path}`",
            f"   - source_doc: `{result.source_path}`",
            f"   - progress_doc: `{result.progress_path}`",
            f"   - source_revision: `{result.source_revision}`",
            f"   - contract_revision: `{result.contract_revision}`",
            f"   - capsule_revision: `{result.capsule_revision}`",
            "   - Do not edit task.md to mark completion; emit evidence and let "
            "the kernel refresh it.",
            "2. Read the Runtime Resume Packet below for next action, missing "
            "evidence, and artifact refs.",
            "3. Read Recent Events, Git State, Currently Active, Role "
            "Instructions, and Causal Chain before emitting any terminal event.",
            "4. If task.md conflicts with event-derived runtime state, trust "
            "events/kanban and suspend for operator attention.",
        ]
        return "\n".join(lines)
    except Exception as exc:
        return (
            f"_(task.md unavailable: {exc})_\n"
            "1. Use Current Task + Runtime Resume Packet as fallback.\n"
            "2. Do not mark completion by editing files; emit the role event."
        )


def _render_resume_packet(
    state_dir: Path,
    task: Task,
    role: str,
    *,
    config: ZfConfig | None = None,
    project_root: Path | None = None,
) -> str:
    try:
        from zf.runtime.long_horizon import build_resume_packet, write_resume_packet

        packet = build_resume_packet(
            state_dir,
            task.id,
            dispatch_id=task.active_dispatch_id or "recovery",
            config=config,
            project_root=project_root,
        )
        path = write_resume_packet(
            state_dir,
            packet,
            dispatch_id=task.active_dispatch_id or "recovery",
        )
        lines = [
            f"- **Path**: `{path}`",
            f"- **Role**: `{role}`",
            f"- **Next event**: {packet.get('next_required_event', '')}",
            f"- **Next**: {packet.get('next_required_action', '')}",
            f"- **Missing evidence**: {len(packet.get('missing_evidence') or [])}",
        ]
        artifact_refs = packet.get("accepted_artifact_refs") or []
        hash_status = packet.get("artifact_hash_status") or []
        missing_refs = packet.get("missing_artifact_refs") or []
        requirements = packet.get("sufficiency_requirements") or {}
        if isinstance(requirements, dict):
            required_refs = requirements.get("required_contract_refs") or []
            if required_refs:
                lines.append(
                    "- **Required contract refs**: "
                    + ", ".join(f"`{item}`" for item in required_refs)
                )
        lines.append(f"- **Accepted artifact refs**: {len(artifact_refs)}")
        ok_hashes = sum(
            1 for item in hash_status
            if isinstance(item, dict) and item.get("status") == "ok"
        )
        failed_hashes = [
            item for item in hash_status
            if isinstance(item, dict) and item.get("status") in {"missing", "mismatch"}
        ]
        lines.append(
            f"- **Artifact hash status**: ok={ok_hashes}, failed={len(failed_hashes)}"
        )
        if missing_refs:
            lines.append(
                "- **Missing artifact refs**: "
                + ", ".join(f"`{item}`" for item in missing_refs)
            )
        lines.extend([
            "",
            "### Recovery Handshake",
            "Before continuing, restate the task id, stage/current state, "
            "next action, do-not-repeat items, and artifact refs you loaded.",
        ])
        return "\n".join(lines)
    except Exception as exc:
        return f"_(resume packet unavailable: {exc})_"


def _render_events(state_dir: Path, role: str, task: Task, limit: int) -> str:
    log_path = state_dir / "events.jsonl"
    if not log_path.exists():
        return "_(no events)_"
    log = EventLog(log_path)
    all_events = log.read_all()
    relevant = [
        e for e in all_events
        if (e.task_id == task.id) or (e.actor == role)
    ]
    relevant = relevant[-limit:]
    if not relevant:
        return "_(no events for this role/task)_"
    return "\n".join(
        f"- `{e.ts}` `{e.type}` actor=`{e.actor or '?'}` task=`{e.task_id or '?'}`"
        for e in relevant
    )


def _render_git(
    git_state: GitState | None,
    git_context: GitDiffContext | None = None,
) -> str:
    if git_context is not None:
        return render_git_diff_context(git_context)
    if git_state is None or (git_state.branch is None and git_state.head is None):
        return "_(no git state captured)_"
    lines = [
        f"- **Branch**: `{git_state.branch}`",
        f"- **HEAD**: `{git_state.head}`",
        f"- **Last commit**: {git_state.last_commit_msg}",
    ]
    if git_state.dirty_files:
        lines.append(f"- **Dirty files**: {', '.join(git_state.dirty_files)}")
    else:
        lines.append("- **Working tree**: clean")
    return "\n".join(lines)


def _render_progress_active(state_dir: Path, max_lines: int) -> str:
    """Extract the ``## Currently Active`` section from progress.md."""
    progress_path = state_dir / "progress.md"
    if not progress_path.exists():
        return "_(no progress.md yet)_"
    text = progress_path.read_text(encoding="utf-8")
    if _CURRENT_ACTIVE_HEADER not in text:
        return "_(no Currently Active section)_"
    after = text.split(_CURRENT_ACTIVE_HEADER, 1)[1]
    # Take everything until the next `## ` heading
    lines = after.splitlines()
    collected: list[str] = []
    for line in lines:
        if line.startswith("## ") and line.strip() != _CURRENT_ACTIVE_HEADER:
            break
        collected.append(line)
        if len(collected) >= max_lines:
            break
    body = "\n".join(collected).strip()
    return body if body else "_(empty)_"


def _render_instructions(state_dir: Path, role: str, max_lines: int) -> str:
    path = state_dir / "instructions" / f"{role}.md"
    if not path.exists():
        return "_(no instructions file — agent defaults)_"
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    if len(lines) > max_lines:
        lines = lines[:max_lines] + [f"_(...truncated {len(text.splitlines()) - max_lines} lines)_"]
    return "\n".join(lines)


def _render_causation_chain(state_dir: Path, task: Task, max_links: int) -> str:
    log_path = state_dir / "events.jsonl"
    if not log_path.exists():
        return "_(no events)_"
    log = EventLog(log_path)
    all_events = log.read_all()
    task_events = [e for e in all_events if e.task_id == task.id]
    if not task_events:
        return "_(no events for this task)_"
    target_id = task_events[-1].id
    chain = log.get_causation_chain(target_id)
    if not chain:
        return "_(no causal ancestors)_"
    trimmed = chain[-max_links:]
    return "\n".join(
        f"- `{e.ts}` `{e.type}` actor=`{e.actor or '?'}`"
        for e in trimmed
    )
