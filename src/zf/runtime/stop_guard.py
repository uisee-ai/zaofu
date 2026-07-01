"""ZF-PWF-STOP-GUARD-001 — provider Stop hook gate evaluator (doc 41 §4.5).

Pure-function gate evaluator: given a Task + event-log query helper,
return the list of missing gates that would prevent a clean
``done`` / ``ship`` transition.

The kernel already enforces gates at dispatch / done time via
``max_rework_attempts`` and discriminator AND-closure. This module
moves the same check **forward** to the provider Stop hook so a worker
that's about to stop without satisfying gates is told "you still need
X" before wasting one full turn.

Discipline:
- This module is **pure**: no side effects, no event emission, no
  file writes. ``hook_recv`` is the side-effecting caller.
- Gate names are stable strings (``review.approved`` / ``test.passed``
  / ``judge.passed`` / ``discriminator.passed`` / ``release.ready``).
- Returns ``[]`` when nothing is missing — caller allows the stop.
- Returns ``[gate, gate, ...]`` when something is missing — caller
  blocks the stop and surfaces the list as continue advice.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Iterable

from zf.core.task.schema import Task


@dataclass(frozen=True)
class StopGuardResult:
    """Result of evaluating stop gates for a task.

    - ``missing``: ordered list of gate identifiers the task has not
      yet satisfied (e.g. ``["review.approved", "test.passed"]``).
    - ``required``: full list of gates this task is required to pass
      (derived from contract.verification_tiers).
    - ``advice``: human-readable line(s) to surface as the hook's
      stderr "continue" message. Empty string if no missing gates.
    """

    missing: list[str]
    required: list[str]
    advice: str

    @property
    def blocked(self) -> bool:
        return bool(self.missing)


# Map verification_tier name → the event type that satisfies it.
# Workers complete a stage by emitting the corresponding success event;
# the stop guard checks whether that event exists for this task.
_TIER_TO_SUCCESS_EVENT: dict[str, str] = {
    "review": "review.approved",
    "test": "test.passed",
    "judge": "judge.passed",
    "discriminator": "discriminator.passed",
    "release": "release.ready",
    "static_gate": "static_gate.passed",
}


def evaluate_stop_gates(
    task: Task | None,
    *,
    has_success_event: Callable[[str, str], bool],
) -> StopGuardResult:
    """Evaluate which required gates are still missing for ``task``.

    Args:
        task: the worker's active task, or ``None``.
        has_success_event: callback that answers
            ``has_success_event(task_id, event_type)`` — True iff
            ``task_id`` has at least one event of ``event_type`` in
            events.jsonl. Caller supplies (the module avoids importing
            EventLog directly so it can be tested without I/O).

    Returns:
        :class:`StopGuardResult` whose ``blocked`` property is True
        when the stop should be blocked.

    Semantics:
    - ``task=None`` → no active task, no gates to enforce. Returns
      empty missing list (allow stop).
    - Task with no ``contract.verification_tiers`` → no specific gate
      list declared; defer to kernel discriminator (allow stop).
    - Otherwise: for every tier in verification_tiers, look up the
      matching success event in the map; mark as missing if no event.
    - ``active_dispatch_id`` empty → mark as missing too (dispatch
      did not record, must not stop).
    """
    if task is None:
        return StopGuardResult(missing=[], required=[], advice="")

    required: list[str] = []
    missing: list[str] = []

    if not task.active_dispatch_id:
        missing.append("active_dispatch_id")

    tiers: Iterable[str] = ()
    contract = getattr(task, "contract", None)
    if contract is not None:
        tiers = getattr(contract, "verification_tiers", []) or []

    for tier in tiers:
        key = str(tier).strip().lower()
        if not key:
            continue
        success_event = _TIER_TO_SUCCESS_EVENT.get(key)
        if success_event is None:
            # Unknown tier name — record as required but don't try to
            # check; the kernel discriminator will handle it.
            continue
        required.append(success_event)
        if not has_success_event(task.id, success_event):
            missing.append(success_event)

    advice = _format_advice(task.id, missing, required)
    return StopGuardResult(
        missing=missing, required=required, advice=advice,
    )


def _format_advice(
    task_id: str,
    missing: list[str],
    required: list[str],
) -> str:
    if not missing:
        return ""
    lines = [
        f"[zf STOP-GUARD] Task {task_id} cannot stop yet — missing gates:",
    ]
    for gate in missing:
        lines.append(f"  - {gate}")
    if required:
        lines.append(
            f"Required gates from contract.verification_tiers: "
            f"{', '.join(required)}"
        )
    lines.append(
        "Emit the corresponding completion event(s) before stopping, "
        "or escalate via *.suspended if blocked."
    )
    return "\n".join(lines)
