"""β-4 (2026-05-17): local multi-task failure fix-task spawn.

When a rework-triggering event (review.rejected / verify.failed /
test.failed / judge.failed / gate.failed) reports a **local CRITICAL** failure
spanning **≥2 task ids**, zaofu's existing rework path requeues the original
task (覆盖式重做 — discards completed work). This path keeps the original task
as ``done`` and creates a new fix-task on the same feature backlog with
``contract.fix_of = origin_task.id``.

Conservative trigger: ONLY when the event payload explicitly carries
``severity``, ``scope``, and ``affected_task_ids`` — empty / missing
fields fall through to standard rework_routing. This avoids fix-task
explosions on current events that don't yet emit the required annotations.

Strategies 1 (systemic redo), 2 (single-task local retry), 4 (minor
flag) are handled by zaofu's existing ``workflow.rework_routing`` and
do not need new wiring — only Strategy 3 is added here.
"""

from __future__ import annotations

import uuid

from zf.core.events.model import ZfEvent
from zf.core.task.schema import Task, TaskContract


_REWORK_TRIGGER_TYPES = frozenset({
    "review.rejected",
    "verify.failed",
    "test.failed",
    "judge.failed",
    "gate.failed",
})


def _safe_payload(event: ZfEvent) -> dict:
    return event.payload if isinstance(event.payload, dict) else {}


def should_spawn_fix_task(event: ZfEvent) -> tuple[bool, str]:
    """Local multi-task failure classifier.

    Returns ``(True, reason)`` only when payload explicitly meets all
    three conditions:

    - ``severity in {"critical", "major"}``
    - ``scope == "local"``
    - ``len(affected_task_ids) >= 2``

    All other cases → ``(False, "")`` (fall through to standard rework).
    """
    if event.type not in _REWORK_TRIGGER_TYPES:
        return False, ""
    payload = _safe_payload(event)
    severity = str(payload.get("severity") or "").strip().lower()
    scope = str(payload.get("scope") or "").strip().lower()
    affected = payload.get("affected_task_ids") or []
    if severity not in {"critical", "major"}:
        return False, ""
    if scope != "local":
        return False, ""
    if not isinstance(affected, list) or len(affected) < 2:
        return False, ""
    return True, (
        f"local-{severity} multi-task ({len(affected)} affected) → "
        f"spawn fix-task instead of rework requeue"
    )


def build_fix_task(parent_task: Task, trigger_event: ZfEvent) -> Task:
    """Construct a new Task linked to ``parent_task`` via
    ``contract.fix_of``. Inherits feature_id; title derived from
    trigger_event payload summary; behavior synthesized to drive the
    fix.

    Caller is responsible for `task_store.add(fix_task)` + emitting
    `task.fix_spawned`.
    """
    parent_contract = parent_task.contract or TaskContract()
    payload = _safe_payload(trigger_event)
    summary = str(payload.get("summary") or "").strip()
    severity = str(payload.get("severity") or "critical").strip()
    title_hint = summary[:60] if summary else f"fix {parent_task.id}"
    title = f"fix({parent_task.id}): {title_hint}"

    contract = TaskContract(
        feature_id=parent_contract.feature_id,
        behavior=(
            f"Fix-task for {parent_task.id}. Trigger: {trigger_event.type} "
            f"(severity={severity}). Original summary: {summary or '-'}. "
            f"Do NOT regress prior work; address the specific failure "
            f"and emit dev.build.done."
        ),
        scope=list(parent_contract.scope or []),
        affected_files=list(parent_contract.affected_files or []),
        shared_files=list(parent_contract.shared_files or []),
        exclusive_files=list(parent_contract.exclusive_files or []),
        owner_role="dev",
        fix_of=parent_task.id,
    )

    new_id = f"TASK-{uuid.uuid4().hex[:6].upper()}"
    return Task(
        id=new_id,
        title=title,
        contract=contract,
        status="ready",
        priority=parent_task.priority,
    )


def build_fix_spawned_event(
    *, parent_task_id: str, fix_task_id: str, trigger_event: ZfEvent,
    reason: str,
) -> ZfEvent:
    """Construct the ``task.fix_spawned`` event for emission. Caller
    responsible for ``event_writer.append(...)``."""
    return ZfEvent(
        type="task.fix_spawned",
        actor="zf-cli",
        task_id=fix_task_id,
        payload={
            "parent_task_id": parent_task_id,
            "fix_task_id": fix_task_id,
            "trigger_event_id": trigger_event.id,
            "trigger_event_type": trigger_event.type,
            "reason": reason,
        },
        causation_id=trigger_event.id,
        correlation_id=trigger_event.correlation_id,
    )
