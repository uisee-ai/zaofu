"""ZF-PWF-MEM-001 — 4-file working-memory projection (doc 41 §4.1, I61).

Renders human-readable Markdown projections of zaofu state on a
per-task basis::

    .zf/projections/tasks/<task_id>/plan.md
    .zf/projections/tasks/<task_id>/findings.md
    .zf/projections/tasks/<task_id>/progress.md
    .zf/projections/tasks/<task_id>/attempt-ledger.md

These files are **projection only, not runtime truth** — every
generated file declares this in its header. They exist so a
fresh-context worker can pick up where the previous one left off
without needing the chat transcript.

Source-of-truth references (audit trail):
- plan.md ← StatePacket + workflow stage_order
- findings.md ← research artifacts + review/test/judge findings
- progress.md ← events list filtered by task
- attempt-ledger.md ← rework + 3-Strike historic events

Discipline:
- File header invariant (per I61):
  ```
  > projection only, not runtime truth
  > source_events: [evt-..., evt-...]
  > state_packet_ref: ./state-packet.json
  > generated_at: <iso8601>
  ```
- Worker may reference projection files in its output but cannot
  write into them. Atomic write only via this module.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from zf.core.state.atomic_io import atomic_write_text
from zf.core.state.state_packet import StatePacket


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class ProjectionInputs:
    """Bundled inputs the renderer reads. Caller supplies what's
    available; missing pieces are rendered as ``(none)`` placeholders
    rather than crashing."""

    packet: StatePacket
    events: tuple = ()
    rework_events: tuple = ()
    research_paths: tuple[str, ...] = ()
    state_packet_ref: str = ""
    source_event_ids: tuple[str, ...] = ()


_PROJECTION_HEADER_TEMPLATE = (
    "> projection only, not runtime truth\n"
    "> source_events: {source_events}\n"
    "> state_packet_ref: {state_packet_ref}\n"
    "> generated_at: {generated_at}\n"
)


def _projection_header(inputs: ProjectionInputs) -> str:
    return _PROJECTION_HEADER_TEMPLATE.format(
        source_events=list(inputs.source_event_ids) if inputs.source_event_ids else "[]",
        state_packet_ref=inputs.state_packet_ref or "(none)",
        generated_at=_now_iso(),
    )


def render_plan(inputs: ProjectionInputs) -> str:
    """plan.md — roadmap + phase status derived from StatePacket."""
    packet = inputs.packet
    lines = [
        f"# Plan · {packet.task_id or '(no task)'}",
        "",
        _projection_header(inputs),
        f"- **Objective**: {packet.objective or '(none)'}",
        f"- **Current stage**: {packet.current_stage or '(unknown)'}",
        f"- **Owner**: {packet.owner.role or '-'}/{packet.owner.instance_id or '-'}",
        f"- **Next event**: {packet.next_event or '(none — ship-ready)'}",
        "",
    ]
    if packet.contract.behavior:
        lines.append("## Behavior")
        lines.append(packet.contract.behavior)
        lines.append("")
    if packet.contract.acceptance:
        lines.append("## Acceptance criteria")
        for item in packet.contract.acceptance:
            lines.append(f"- [ ] {item}")
        lines.append("")
    if packet.contract.out_of_scope:
        lines.append("## Out of scope")
        for item in packet.contract.out_of_scope:
            lines.append(f"- {item}")
        lines.append("")
    if packet.completed:
        lines.append("## Completed milestones (chronological)")
        for item in packet.completed:
            lines.append(f"- [x] {item}")
        lines.append("")
    if packet.blocked_by:
        lines.append("## Blocked by")
        for item in packet.blocked_by:
            lines.append(f"- {item}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def render_findings(inputs: ProjectionInputs) -> str:
    """findings.md — research + review/test/judge findings."""
    packet = inputs.packet
    lines = [
        f"# Findings · {packet.task_id or '(no task)'}",
        "",
        _projection_header(inputs),
    ]
    if inputs.research_paths:
        lines.append("## Research artifacts")
        for path in inputs.research_paths:
            lines.append(f"- `{path}`")
        lines.append("")
    if packet.evidence:
        lines.append("## Evidence (from State Packet)")
        for ev in packet.evidence:
            lines.append(
                f"- **{ev.kind}** · {ev.status} · `{ev.path or '-'}`"
                f" (event: {ev.event_id or '-'})"
            )
        lines.append("")
    if packet.risks:
        lines.append("## Risks")
        for r in packet.risks:
            lines.append(f"- {r}")
        lines.append("")
    if (
        not inputs.research_paths
        and not packet.evidence
        and not packet.risks
    ):
        lines.append("_No findings recorded yet._")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def render_progress(inputs: ProjectionInputs) -> str:
    """progress.md — chronological event summary + actions log."""
    packet = inputs.packet
    lines = [
        f"# Progress · {packet.task_id or '(no task)'}",
        "",
        _projection_header(inputs),
    ]
    progress_events = [
        ev for ev in inputs.events
        if getattr(ev, "type", "") in {"worker.progress", "phase.progressed"}
    ]
    if progress_events:
        lines.append("## Structured progress")
        for ev in progress_events[-20:]:
            payload = getattr(ev, "payload", {}) or {}
            payload = payload if isinstance(payload, dict) else {}
            phase = str(payload.get("phase") or "-")
            message = str(payload.get("message") or payload.get("current_subtask") or "")
            percent = payload.get("percent")
            percent_text = f" · {percent}%" if percent is not None else ""
            lines.append(
                f"- `{getattr(ev, 'ts', '')}` · **{phase}**{percent_text}"
                f" · {message or getattr(ev, 'type', '')}"
            )
        lines.append("")
    if inputs.events:
        lines.append("## Recent events")
        for ev in inputs.events[-50:]:
            etype = getattr(ev, "type", "")
            ts = getattr(ev, "ts", "")
            eid = getattr(ev, "id", "")
            lines.append(f"- `{ts}` · **{etype}** · {eid}")
        lines.append("")
    if packet.decisions:
        lines.append("## Decisions")
        for d in packet.decisions:
            lines.append(f"- {d}")
        lines.append("")
    if not inputs.events and not packet.decisions:
        lines.append("_No progress recorded yet._")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def render_attempt_ledger(inputs: ProjectionInputs) -> str:
    """attempt-ledger.md — rework + 3-Strike retry history."""
    packet = inputs.packet
    lines = [
        f"# Attempt Ledger · {packet.task_id or '(no task)'}",
        "",
        _projection_header(inputs),
        "Tracks rework events + failed approaches. Read this before"
        " retrying — don't repeat what's already failed.",
        "",
    ]
    if inputs.rework_events:
        lines.append("## Rework attempts")
        for ev in inputs.rework_events:
            etype = getattr(ev, "type", "")
            ts = getattr(ev, "ts", "")
            payload = getattr(ev, "payload", {}) or {}
            reason = ""
            if isinstance(payload, dict):
                reason = str(
                    payload.get("reason")
                    or payload.get("notes")
                    or payload.get("classification")
                    or ""
                )
            lines.append(f"- `{ts}` · **{etype}** — {reason or '(no reason)'}")
        lines.append("")
    else:
        lines.append("_No rework attempts yet._")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


def projection_dir(state_dir: Path, task_id: str) -> Path:
    return state_dir / "projections" / "tasks" / task_id


def write_projection_files(
    state_dir: Path,
    inputs: ProjectionInputs,
) -> dict[str, Path]:
    """Atomically write all 4 projection files for the input's task.

    Returns ``{kind: path}`` for the 4 files written. Skips writing
    when task_id is empty (no-task projector run).
    """
    task_id = inputs.packet.task_id
    if not task_id:
        return {}
    target = projection_dir(state_dir, task_id)
    target.mkdir(parents=True, exist_ok=True)
    files: dict[str, Path] = {}
    plan = target / "plan.md"
    atomic_write_text(plan, render_plan(inputs))
    files["plan"] = plan
    findings = target / "findings.md"
    atomic_write_text(findings, render_findings(inputs))
    files["findings"] = findings
    progress = target / "progress.md"
    atomic_write_text(progress, render_progress(inputs))
    files["progress"] = progress
    ledger = target / "attempt-ledger.md"
    atomic_write_text(ledger, render_attempt_ledger(inputs))
    files["attempt_ledger"] = ledger
    return files


# ---------------------------------------------------------------------------
# Discovery helper for downstream callers
# ---------------------------------------------------------------------------


def list_projection_files(state_dir: Path, task_id: str) -> list[Path]:
    """Return on-disk projection files for a task in canonical order."""
    target = projection_dir(state_dir, task_id)
    if not target.exists():
        return []
    order = ["plan.md", "findings.md", "progress.md", "attempt-ledger.md"]
    return [target / name for name in order if (target / name).exists()]
