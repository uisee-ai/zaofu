"""Feature completion projection for task terminal transitions."""

from __future__ import annotations

import re
from pathlib import Path

from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.core.feature.store import FeatureStore, TERMINAL_STATES as FEATURE_TERMINAL
from zf.core.task.schema import Task
from zf.core.task.store import TaskStore, TERMINAL_STATES as TASK_TERMINAL


_FEATURE_KEY_RE = re.compile(r"^(F-[A-Za-z0-9]+)(?::|-|$)")


def close_feature_if_all_tasks_done(
    *,
    state_dir: Path,
    task: Task,
    task_store: TaskStore,
    event_writer: EventWriter,
    event_log: EventLog | None = None,
    actor: str = "zf-cli",
    source: str = "task_terminal_projection",
    trigger_event: str = "",
) -> str | None:
    """Close the parent feature once no linked active tasks remain.

    Feature linkage is intentionally projection-only: the canonical task
    schema does not carry ``feature_id``. We infer it from the task key
    prefix used by Layer 2 (``F-xxxx:task-key``), then fall back to task
    events such as ``task.contract.update`` with ``payload.feature_id``.
    """
    event_feature_ids = _feature_ids_by_task(event_log)
    feature_id = _feature_id_for_task(task, event_feature_ids)
    if not feature_id:
        return None

    feature_store = FeatureStore(state_dir / "feature_list.json")
    feature = feature_store.get(feature_id)
    if feature is None or feature.status in FEATURE_TERMINAL:
        return None

    for active_task in task_store.list_all():
        if active_task.status in TASK_TERMINAL:
            continue
        if active_task.id == task.id:
            continue
        if _feature_id_for_task(active_task, event_feature_ids) == feature_id:
            return None

    updated = feature_store.update(feature_id, status="done")
    if updated is None:
        return None

    payload = {
        "feature_id": feature_id,
        "from": feature.status,
        "to": "done",
        "source": source,
        "trigger_task_id": task.id,
    }
    if trigger_event:
        payload["trigger_event"] = trigger_event
    event_writer.append(ZfEvent(
        type="feature.status_changed",
        actor=actor,
        task_id=feature_id,
        payload=payload,
    ))
    return feature_id


def feature_id_for_task(
    task: Task,
    event_feature_ids: dict[str, str],
) -> str:
    """Return the feature id linked to a task, if any."""
    return _feature_id_for_task(task, event_feature_ids)


def feature_ids_by_task(event_log: EventLog | None) -> dict[str, str]:
    """Return task_id -> feature_id links derived from task events."""
    return _feature_ids_by_task(event_log)


def _feature_id_for_task(
    task: Task,
    event_feature_ids: dict[str, str],
) -> str:
    if task.key:
        match = _FEATURE_KEY_RE.match(task.key)
        if match:
            return match.group(1)
    return event_feature_ids.get(task.id, "")


def _feature_ids_by_task(event_log: EventLog | None) -> dict[str, str]:
    if event_log is None:
        return {}
    out: dict[str, str] = {}
    try:
        events = event_log.read_all()
    except Exception:
        return {}
    for event in events:
        if not event.task_id or not isinstance(event.payload, dict):
            continue
        feature_id = event.payload.get("feature_id")
        contract = event.payload.get("contract")
        if not feature_id and isinstance(contract, dict):
            feature_id = contract.get("feature_id")
        if isinstance(feature_id, str) and feature_id.startswith("F-"):
            out[event.task_id] = feature_id
    return out
