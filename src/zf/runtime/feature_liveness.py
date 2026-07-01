"""Feature-level liveness sweep for long-horizon delivery."""

from __future__ import annotations

from pathlib import Path

from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.core.feature.store import FeatureStore, TERMINAL_STATES as FEATURE_TERMINAL
from zf.core.task.store import TaskStore, TERMINAL_STATES as TASK_TERMINAL
from zf.runtime.feature_completion import feature_id_for_task, feature_ids_by_task


_ACTIVE_TASK_STATUSES = {"in_progress", "review", "testing"}


def sweep_feature_liveness(
    *,
    state_dir: Path,
    task_store: TaskStore,
    event_log: EventLog,
    event_writer: EventWriter,
) -> list[ZfEvent]:
    """Repair or surface active features that no longer have a live path.

    The task lifecycle already protects individual handoffs. This sweep
    protects the feature boundary: an active feature must either have a
    runnable/in-flight task, be closed because all linked tasks are terminal,
    or carry an explicit blocker event for operator/Layer 2 recovery.
    """
    feature_store = FeatureStore(state_dir / "feature_list.json")
    features = [
        feature for feature in feature_store.list_all()
        if feature.status == "active"
    ]
    if not features:
        return []

    emitted: list[ZfEvent] = []
    links = feature_ids_by_task(event_log)
    active_tasks = task_store.list_all()
    all_tasks = task_store.list_all_with_archive()
    ready_ids = {task.id for task in task_store.ready()}
    prior_blocks = _prior_feature_liveness_blocks(event_log)

    for feature in features:
        if feature.status in FEATURE_TERMINAL:
            continue

        active_linked = [
            task for task in active_tasks
            if feature_id_for_task(task, links) == feature.id
        ]
        all_linked = [
            task for task in all_tasks
            if feature_id_for_task(task, links) == feature.id
        ]

        if not active_linked:
            if all_linked and all(
                task.status in TASK_TERMINAL for task in all_linked
            ):
                updated = feature_store.update(feature.id, status="done")
                if updated is None:
                    continue
                emitted.append(event_writer.append(ZfEvent(
                    type="feature.status_changed",
                    actor="zf-cli",
                    task_id=feature.id,
                    payload={
                        "feature_id": feature.id,
                        "from": feature.status,
                        "to": "done",
                        "source": "feature_liveness_sweep",
                    },
                )))
                continue
            emitted.extend(_emit_block_once(
                event_writer=event_writer,
                prior_blocks=prior_blocks,
                feature_id=feature.id,
                reason="active feature has no linked tasks",
                task_ids=[],
            ))
            continue

        if _has_live_path(active_linked, ready_ids):
            continue

        emitted.extend(_emit_block_once(
            event_writer=event_writer,
            prior_blocks=prior_blocks,
            feature_id=feature.id,
            reason="active feature has linked tasks but no runnable path",
            task_ids=[task.id for task in active_linked],
        ))

    return emitted


def _has_live_path(tasks: list, ready_ids: set[str]) -> bool:
    for task in tasks:
        if task.status == "backlog" and task.id in ready_ids:
            return True
        if task.status in _ACTIVE_TASK_STATUSES and task.assigned_to:
            return True
        if task.status in {"review", "testing"}:
            return True
    return False


def _prior_feature_liveness_blocks(event_log: EventLog) -> set[tuple[str, str]]:
    out: set[tuple[str, str]] = set()
    try:
        events = event_log.read_days(1)
    except Exception:
        return out
    for event in events:
        if event.type != "feature.liveness.blocked":
            continue
        if not isinstance(event.payload, dict):
            continue
        feature_id = str(event.payload.get("feature_id") or event.task_id or "")
        reason = str(event.payload.get("reason") or "")
        if feature_id and reason:
            out.add((feature_id, reason))
    return out


def _emit_block_once(
    *,
    event_writer: EventWriter,
    prior_blocks: set[tuple[str, str]],
    feature_id: str,
    reason: str,
    task_ids: list[str],
) -> list[ZfEvent]:
    key = (feature_id, reason)
    if key in prior_blocks:
        return []
    prior_blocks.add(key)
    block = event_writer.append(ZfEvent(
        type="feature.liveness.blocked",
        actor="zf-cli",
        task_id=feature_id,
        payload={
            "feature_id": feature_id,
            "reason": reason,
            "task_ids": task_ids,
            "action_required": "decompose_or_unblock_feature",
        },
    ))
    escalate = event_writer.append(ZfEvent(
        type="human.escalate",
        actor="zf-cli",
        task_id=feature_id,
        payload={
            "feature_id": feature_id,
            "reason": reason,
            "source": "feature_liveness_sweep",
            "task_ids": task_ids,
        },
        causation_id=block.id,
        correlation_id=block.correlation_id,
    ))
    return [block, escalate]
