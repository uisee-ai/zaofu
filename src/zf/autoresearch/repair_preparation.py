"""Autoresearch repair PREPARATION — the safe half of self-repair (doc 78 O-2).

Autoresearch detects ZaoFu's own kernel bugs and produces a repair candidate
whose fix scope is ``src/zf/**`` + ``tests/**`` — i.e. the harness modifying its
OWN code. Auto-APPLYING such an agent-generated kernel patch (verified only by a
focused pytest) would let the harness silently corrupt itself, so that is a
deliberate safety boundary we keep: a human reviews and applies.

This module builds the PREPARED-FOR-APPROVAL record that elevates a buried
proposal into a first-class, human-actionable item with the safety invariant
codified in code. It does NOT execute, apply, or merge anything.

Invariant (not configurable): ``auto_apply`` is always False and
``requires_human_approval`` is always True. There is no code path that sets them
otherwise — removing the human apply gate would require deleting this guarantee.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

AUTO_PREPARE_ENV = "ZF_AUTORESEARCH_AUTO_PREPARE"
REPAIR_PREPARED_EVENT = "autoresearch.repair.prepared"


def auto_prepare_enabled(env: dict[str, str] | None = None) -> bool:
    """Opt-in: elevate autoresearch repair candidates to a prepared-for-approval
    record. Default off → behavior unchanged."""
    src = os.environ if env is None else env
    value = str(src.get(AUTO_PREPARE_ENV) or "").strip().lower()
    return value in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class RepairPreparation:
    candidate_id: str
    repair_task_id: str
    hypothesis: str
    candidate_path: str
    scope: tuple[str, ...] = field(default_factory=tuple)
    verification: str = ""
    # SAFETY INVARIANT — constants, never configurable. The harness does not
    # auto-apply repairs to its own kernel; a human reviews + applies.
    auto_apply: bool = False
    requires_human_approval: bool = True

    @property
    def next_step(self) -> str:
        return (
            f"Review candidate {self.candidate_path}; approve to dispatch the "
            f"isolated repair task {self.repair_task_id} (scope "
            f"{','.join(self.scope) or 'src/zf'}); the verified fix is surfaced "
            f"for MANUAL merge — it is never auto-applied to the running harness."
        )


def build_repair_preparation(bug_candidate_payload: dict[str, Any]) -> RepairPreparation | None:
    """Assemble a RepairPreparation from an autoresearch.bug_candidate.created
    payload. Returns None if the payload lacks a usable repair_task_payload."""
    if not isinstance(bug_candidate_payload, dict):
        return None
    candidate = bug_candidate_payload.get("candidate")
    candidate = candidate if isinstance(candidate, dict) else {}
    repair = bug_candidate_payload.get("repair_task_payload")
    repair = repair if isinstance(repair, dict) else {}
    repair_task_id = str(repair.get("task_id") or "").strip()
    if not repair_task_id:
        return None
    contract = repair.get("contract") if isinstance(repair.get("contract"), dict) else {}
    scope = tuple(str(p) for p in (contract.get("scope") or []) if str(p))
    return RepairPreparation(
        candidate_id=str(candidate.get("candidate_id") or repair.get("key") or ""),
        repair_task_id=repair_task_id,
        hypothesis=str(candidate.get("hypothesis") or repair.get("title") or ""),
        candidate_path=str(bug_candidate_payload.get("candidate_path") or ""),
        scope=scope,
        verification=str(contract.get("verification") or ""),
    )


def repair_prepared_payload(prep: RepairPreparation) -> dict[str, Any]:
    """Event payload for autoresearch.repair.prepared — a human-actionable record
    with the apply gate codified. Never carries an auto-apply instruction."""
    return {
        "candidate_id": prep.candidate_id,
        "repair_task_id": prep.repair_task_id,
        "hypothesis": prep.hypothesis,
        "candidate_path": prep.candidate_path,
        "scope": list(prep.scope),
        "verification": prep.verification,
        "auto_apply": False,
        "requires_human_approval": True,
        "next_step": prep.next_step,
    }


def owner_message_for_prepared_repair(prep: RepairPreparation) -> dict[str, Any]:
    """An owner.visible_message.requested payload so the prepared repair reaches
    the operator (e.g. Feishu via the O-7 auto-delivery). Surfacing only — it
    asks the human to review and apply, never applies."""
    return {
        "message_id": f"repair-prepared-{prep.repair_task_id}",
        "severity": "high",
        "route": "owner",
        "title": "Autoresearch repair prepared — review & apply",
        "text": (
            f"A harness self-repair is prepared (NOT auto-applied).\n"
            f"hypothesis: {prep.hypothesis}\n"
            f"task: {prep.repair_task_id}  scope: {','.join(prep.scope) or 'src/zf'}\n"
            f"{prep.next_step}"
        ),
    }
