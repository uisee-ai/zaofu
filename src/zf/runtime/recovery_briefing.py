"""ZF-CONTEXT-REC-001 — recovery briefing from State Packet (doc 39 §4.3).

When a worker recovers from PreCompact / context-exhausted / respawn /
recycle, the briefing must NOT rely on session resume alone. Doc 39 §4.3
+ §9 hard invariants:

    Chat history can be discarded.
    State Packet can resume the next agent.

This module renders a recovery briefing from a freshly-projected
State Packet + optional Catchup evidence (PWF-CATCHUP-001) +
projection_refs (PWF-MEM-001). Output is what gets sent into the
new worker session.
"""

from __future__ import annotations

from typing import Iterable

from zf.core.state.state_packet import StatePacket
from zf.runtime.backend_session_reader import TranscriptCatchup
from zf.runtime.workflow_state_breadcrumb import (
    render_workflow_state_breadcrumb,
)


def render_recovery_briefing(
    packet: StatePacket,
    *,
    dispatch_id: str = "",
    state_packet_ref: str = "",
    projection_refs: Iterable[str] = (),
    catchup: TranscriptCatchup | None = None,
    recovery_reason: str = "",
) -> str:
    """Build the full recovery briefing string.

    Structure (top → bottom, so worker sees most-important first):

    1. ``Active task: <task_id>``      (I55 marker)
    2. Recovery banner with reason
    3. ``<zf-workflow-state>`` breadcrumb (WFSTATE-001)
    4. Catchup evidence (CATCHUP-001) — if present, marked as
       *evidence not truth*
    5. State Packet markdown (SP-001) — full plan
    6. Projection refs (MEM-001) — file paths the worker can
       re-read
    7. Instructions

    Args:
        packet: freshly projected StatePacket.
        dispatch_id: current dispatch id (empty during cold respawn).
        state_packet_ref: path to state-packet.json on disk.
        projection_refs: 4-file projection paths (plan / findings /
            progress / attempt-ledger).
        catchup: optional TranscriptCatchup with "what happened since
            the snapshot" — informational only.
        recovery_reason: short tag e.g. ``"precompact"`` /
            ``"context_exhausted"`` / ``"respawn"`` for the banner.
    """
    lines: list[str] = []
    if packet.task_id:
        lines.append(f"Active task: {packet.task_id}")
    else:
        lines.append("Active task: (none — see recovery banner)")
    lines.append("")
    lines.append("## Recovery briefing")
    lines.append(
        f"This worker was restarted (reason: "
        f"`{recovery_reason or 'unspecified'}`). "
        f"Chat history may be incomplete. The State Packet below is "
        f"the canonical fact source — trust it over any partial "
        f"transcript memory."
    )
    lines.append("")
    lines.append(render_workflow_state_breadcrumb(
        packet,
        dispatch_id=dispatch_id,
        state_packet_ref=state_packet_ref,
        projection_refs=projection_refs,
    ))
    lines.append("")
    if catchup is not None:
        lines.append("## Transcript catchup (evidence, NOT truth)")
        lines.append(
            "The previous worker session emitted the following after "
            f"timestamp `{catchup.since_timestamp}`. Treat these as "
            "evidence for human / operator review — do not act on them "
            "as if they were State Packet content."
        )
        lines.append("")
        if catchup.new_user_messages:
            lines.append("### New user messages")
            for m in catchup.new_user_messages[:5]:
                lines.append(f"- {m[:200]}")
            lines.append("")
        if catchup.new_tool_uses:
            lines.append("### Tools used since snapshot")
            for t in catchup.new_tool_uses[:10]:
                lines.append(f"- {t}")
            lines.append("")
        if catchup.new_edits:
            lines.append("### Files edited since snapshot")
            for f in catchup.new_edits[:20]:
                lines.append(f"- `{f}`")
            lines.append("")
        if catchup.new_errors:
            lines.append("### Errors since snapshot")
            for e in catchup.new_errors[:10]:
                lines.append(f"- {e[:200]}")
            lines.append("")
    # State Packet body (re-use the projector's markdown render via
    # a thin call — but render_md depends on Projector instance, so
    # we inline a minimal render here to keep module side-effect free)
    lines.extend(_packet_quick_render(packet))
    lines.append("")
    if projection_refs:
        lines.append("## Projection files (re-read at any time)")
        for ref in projection_refs:
            lines.append(f"- `{ref}`")
        lines.append("")
    lines.append("## Resume instructions")
    lines.append(
        "1. Re-read the State Packet + projection files above. "
        "Don't rely on conversation memory.\n"
        "2. Identify your next required event from "
        "`required_next_event` in the breadcrumb.\n"
        "3. If gates are missing or you're uncertain, emit "
        "`memory.note category=recovery_uncertain` and ask for "
        "operator clarification — do NOT guess."
    )
    return "\n".join(lines).rstrip() + "\n"


def _packet_quick_render(packet: StatePacket) -> list[str]:
    """Quick inline render of the State Packet contents — enough for
    recovery briefing without importing the projector module."""
    lines: list[str] = []
    lines.append("## State Packet")
    lines.append(
        f"- task_id: `{packet.task_id or '-'}`  ·  "
        f"stage: `{packet.current_stage or '-'}`  ·  "
        f"next_event: `{packet.next_event or '(ship-ready)'}`"
    )
    if packet.objective:
        lines.append(f"- objective: {packet.objective}")
    if packet.contract.behavior:
        lines.append(f"- behavior: {packet.contract.behavior[:300]}")
    if packet.completed:
        lines.append("- completed: " + ", ".join(packet.completed))
    if packet.evidence:
        lines.append("- evidence:")
        for ev in packet.evidence:
            lines.append(
                f"  - {ev.kind}/{ev.status} `{ev.path or '-'}` "
                f"({ev.event_id or '-'})"
            )
    if packet.blocked_by:
        lines.append("- blocked_by: " + ", ".join(packet.blocked_by))
    return lines
