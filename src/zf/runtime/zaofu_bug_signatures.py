"""β-1 (2026-05-17): zaofu failure-signature detection.

Periodic scan over recent events to spot **known zaofu kernel failure
patterns** (e.g. ship loop, respawn cascade, judge whack-a-mole). On
match the orchestrator emits ``zaofu.bug.detected`` with structured
``cangjie_state_snapshot`` so the operator playbook (β-2) and the
``zf bug-fix-cycle`` CLI (β-3) can drive an off-line fix cycle.

Validate-First Discipline (CLAUDE.md): these signatures are seeded
by **actual cangjie r-next-8 / r-next-9 failure patterns**, not
speculation. Each test_*_signature lives in
tests/test_zaofu_bug_signatures.py with a replay-style fixture.

This module is **pure**: each detector function takes a list of recent
``ZfEvent`` and returns ``SignatureMatch | None``. No I/O, no event
emission — the orchestrator wraps the scan.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from zf.core.events.model import ZfEvent


@dataclass(frozen=True)
class SignatureMatch:
    """A detected zaofu failure pattern with enough evidence for the
    operator playbook to drive a precise fix cycle."""
    signature: str                       # e.g. "ship_block_loop"
    confidence: str                      # "high" | "medium" | "low"
    evidence_event_ids: list[str]        # the raw events that matched
    suggested_fix_area: str              # file:symbol hint
    cangjie_state_snapshot: dict = field(default_factory=dict)


def _safe_payload(event: ZfEvent) -> dict:
    return event.payload if isinstance(event.payload, dict) else {}


# ─── ship_block_loop ─────────────────────────────────────────────────────

def ship_block_loop_signature(events: list[ZfEvent]) -> SignatureMatch | None:
    """≥2 ``ship.blocked`` events with **identical blockers** within
    the recent window. Cangjie r-next-8 + r-next-9 both ended this way
    on the same ``["working tree is dirty", "candidate is not ready"]``
    — that pair fix is commit ``96ba665`` (B-NEW-13 + B-NEW-14). Future
    ship-blocker patterns reuse this detector."""
    blocked = [e for e in events if e.type == "ship.blocked"]
    if len(blocked) < 2:
        return None
    # Group by (blockers tuple, pdd_id) to detect a true loop.
    groups: dict[tuple[tuple[str, ...], str], list[ZfEvent]] = {}
    for ev in blocked:
        p = _safe_payload(ev)
        blockers = tuple(sorted(str(b) for b in (p.get("blockers") or [])))
        pdd_id = str(p.get("pdd_id") or "")
        groups.setdefault((blockers, pdd_id), []).append(ev)
    for (blockers, pdd_id), group in groups.items():
        if len(group) >= 2 and blockers:
            return SignatureMatch(
                signature="ship_block_loop",
                confidence="high" if len(group) >= 3 else "medium",
                evidence_event_ids=[e.id for e in group],
                suggested_fix_area="src/zf/runtime/ship.py",
                cangjie_state_snapshot={
                    "pdd_id": pdd_id,
                    "blockers": list(blockers),
                    "occurrence_count": len(group),
                },
            )
    return None


# ─── respawn_failure_cascade ─────────────────────────────────────────────

def respawn_failure_cascade_signature(
    events: list[ZfEvent],
) -> SignatureMatch | None:
    """≥2 ``worker.respawn.failed`` events in the recent window.
    Cangjie r-next-9 (B-NEW-21 candidate): worker.stuck triggers
    respawn, respawn fails repeatedly, system cascades to
    blocked_human."""
    failed = [e for e in events if e.type == "worker.respawn.failed"]
    if len(failed) < 2:
        return None
    instances: dict[str, list[ZfEvent]] = {}
    for ev in failed:
        instances.setdefault(ev.actor or "", []).append(ev)
    for instance_id, group in instances.items():
        if len(group) >= 2:
            return SignatureMatch(
                signature="respawn_failure_cascade",
                confidence="high" if len(group) >= 3 else "medium",
                evidence_event_ids=[e.id for e in group],
                suggested_fix_area="src/zf/runtime/spawn_coordinator.py",
                cangjie_state_snapshot={
                    "instance_id": instance_id,
                    "occurrence_count": len(group),
                },
            )
    return None


# ─── judge_failure_loop ──────────────────────────────────────────────────

def judge_failure_loop_signature(events: list[ZfEvent]) -> SignatureMatch | None:
    """≥3 ``judge.failed`` events for the **same task** with overlapping
    rejection summaries. B-NEW-12 family: judge whack-a-mole that doesn't
    self-heal through evidence_reissue."""
    by_task: dict[str, list[ZfEvent]] = {}
    for ev in events:
        if ev.type != "judge.failed" or not ev.task_id:
            continue
        by_task.setdefault(ev.task_id, []).append(ev)
    for task_id, group in by_task.items():
        if len(group) >= 3:
            # Cheap overlap check: if all summaries identical OR share
            # the first 30 chars, treat as repeat pattern.
            summaries = [
                str(_safe_payload(e).get("summary") or "")[:30] for e in group
            ]
            if len(set(summaries)) <= max(1, len(group) // 2):
                return SignatureMatch(
                    signature="judge_failure_loop",
                    confidence="high" if len(group) >= 4 else "medium",
                    evidence_event_ids=[e.id for e in group],
                    suggested_fix_area=(
                        "src/zf/core/verification/discriminator.py "
                        "or judge prompt template"
                    ),
                    cangjie_state_snapshot={
                        "task_id": task_id,
                        "occurrence_count": len(group),
                        "rejection_summaries": summaries,
                    },
                )
    return None


# ─── registry + scan helper ──────────────────────────────────────────────

ALL_SIGNATURES: tuple[Callable[[list[ZfEvent]], SignatureMatch | None], ...] = (
    ship_block_loop_signature,
    respawn_failure_cascade_signature,
    judge_failure_loop_signature,
)


def scan_zaofu_bugs(events: list[ZfEvent]) -> list[SignatureMatch]:
    """Run every signature; return the list of non-None matches.

    Designed for periodic invocation (~5 min cadence) from the
    orchestrator tick. Cheap (linear in events count × signature
    count); safe to call frequently. Caller is responsible for
    deduplication if the same SignatureMatch fired in the prior tick
    (typically: orchestrator caches last-emitted evidence ids).
    """
    out: list[SignatureMatch] = []
    for signature_fn in ALL_SIGNATURES:
        try:
            match = signature_fn(events)
        except Exception:
            match = None
        if match is not None:
            out.append(match)
    return out
