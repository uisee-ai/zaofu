"""Level-triggered affinity writer slot reconciliation."""

from __future__ import annotations

from typing import Any

from zf.core.events.model import ZfEvent
from zf.runtime.fanout import FanoutChild, FanoutContext
from zf.runtime.lane_stage_handoff import per_lane_flow_match
from zf.runtime.orchestrator_fanout import _writer_task_dependencies_satisfied


def reconcile_active_affinity_writer_fanouts(orchestrator: Any) -> int:
    fanout_root = orchestrator.state_dir / "fanouts"
    if not fanout_root.exists():
        return 0
    terminal_statuses = {"completed", "failed", "timed_out", "cancelled"}
    dispatched = 0
    for manifest_path in sorted(fanout_root.glob("*/manifest.json")):
        fanout_id = manifest_path.parent.name
        manifest = orchestrator._fanout_manifest(fanout_id)
        if not manifest or manifest.get("topology") != "fanout_writer_scoped":
            continue
        aggregate = (
            manifest.get("aggregate")
            if isinstance(manifest.get("aggregate"), dict)
            else {}
        )
        if (
            str(manifest.get("status") or "") in terminal_statuses
            or str(aggregate.get("status") or "") in terminal_statuses
        ):
            continue
        stage = orchestrator._fanout_stage_by_id(str(manifest.get("stage_id") or ""))
        if stage is None or (
            orchestrator._fanout_assignment_strategy(stage) != "affinity_stage_slots"
        ):
            continue
        stage_slot = str(
            getattr(getattr(stage, "assignment", None), "stage_slot", "") or ""
        )
        if not stage_slot:
            continue
        dispatched += reconcile_affinity_writer_slots(
            orchestrator,
            fanout_id=fanout_id,
            stage=stage,
            stage_slot=stage_slot,
            causation_id=str(manifest.get("trigger_event_id") or ""),
        )
    return dispatched


def reconcile_affinity_writer_slots(
    orchestrator: Any,
    *,
    fanout_id: str,
    stage: Any,
    stage_slot: str,
    causation_id: str,
) -> int:
    manifest = orchestrator._fanout_manifest(fanout_id)
    if not manifest or _fanout_terminal(manifest):
        return 0
    superseded_by = _newer_writer_replan_fanout(
        orchestrator,
        fanout_id=fanout_id,
        manifest=manifest,
    )
    if superseded_by:
        orchestrator._cancel_superseded_fanout_manifest(
            fanout_id=fanout_id,
            manifest=manifest,
            reason="superseded_by_newer_replan_attempt",
            superseded_by=superseded_by,
            source="writer_slot_reconcile_generation_guard",
        )
        return 0
    lane_roles = orchestrator._fanout_affinity_lane_roles(stage)
    occupied_lane_ids = _occupied_lane_ids(manifest, stage_slot=stage_slot)
    free_lane_count = sum(
        lane_id not in occupied_lane_ids for lane_id, _role in lane_roles
    )
    if free_lane_count <= 0:
        return 0
    queued_children = [
        child
        for child in manifest.get("children", []) or []
        if isinstance(child, dict)
        and str(child.get("status") or "") == "queued"
        and str(child.get("assignment_strategy") or "") == "affinity_stage_slots"
        and str(child.get("stage_slot") or "") == stage_slot
    ]
    if not queued_children:
        return 0
    # Legacy single-stage fanouts have no lane pipeline to close TaskStore, so
    # their completed writer child is the terminal dependency fact.  In a
    # per-lane pipeline, an impl child still has review/verify work ahead; only
    # canonical task terminal may release downstream tasks.
    completed_task_ids = set()
    if per_lane_flow_match(orchestrator.config, stage.id, stage_slot) is None:
        completed_task_ids = {
            str(child.get("task_id") or "")
            for child in manifest.get("children", []) or []
            if isinstance(child, dict)
            and str(child.get("status") or "") == "completed"
            and str(child.get("task_id") or "")
        }
    context = FanoutContext(
        fanout_id=fanout_id,
        stage_id=str(manifest.get("stage_id") or ""),
        topology=str(manifest.get("topology") or "fanout_writer_scoped"),
        trace_id=str(manifest.get("trace_id") or ""),
        trigger_event_id=str(manifest.get("trigger_event_id") or ""),
        target_ref=str(manifest.get("target_ref") or ""),
        expected_children=[],
    )
    dispatched = 0
    used_lane_ids = set(occupied_lane_ids)
    for queued in sorted(queued_children, key=_queue_key):
        if dispatched >= free_lane_count:
            break
        child_payload = _queued_child_payload(queued)
        if not _writer_task_dependencies_satisfied(
            orchestrator.task_store,
            child_payload,
            completed_task_ids=completed_task_ids,
        ):
            continue
        selected = orchestrator._select_writer_affinity_lane_role(
            stage,
            child_payload,
            lane_roles=lane_roles,
            used_lane_ids=used_lane_ids,
        )
        if selected is None:
            break
        lane_id, role = selected
        used_lane_ids.add(lane_id)
        child_payload.update({
            "assignment_strategy": "affinity_stage_slots",
            "lane_profile": str(queued.get("lane_profile") or ""),
            "lane_id": lane_id,
            "stage_slot": stage_slot,
            "affinity_tag": str(queued.get("affinity_tag") or ""),
            "role_instance": role.instance_id,
        })
        child = FanoutChild(
            child_id=str(queued.get("child_id") or ""),
            role_instance=role.instance_id,
            target_ref=str(
                queued.get("target_ref") or manifest.get("target_ref") or ""
            ),
            payload=child_payload,
        )
        slot_payload = {
            "fanout_id": fanout_id,
            "trace_id": context.trace_id,
            "stage_id": context.stage_id,
            "child_id": child.child_id,
            "role_instance": role.instance_id,
            "task_id": str(child_payload.get("task_id") or ""),
        }
        orchestrator._copy_fanout_assignment_metadata(slot_payload, child_payload)
        assigned_event = orchestrator.event_writer.append(ZfEvent(
            type="fanout.slot.assigned",
            actor="zf-cli",
            payload=slot_payload,
            causation_id=causation_id,
            correlation_id=context.trace_id,
        ))
        orchestrator._unpark_writer_fanout_queued_task(
            str(child_payload.get("task_id") or "")
        )
        sent = orchestrator._dispatch_writer_fanout_child(
            context=context,
            child=child,
            task_item=child_payload,
            role=role,
            pdd_id=str(queued.get("pdd_id") or manifest.get("pdd_id") or ""),
            feature_id=str(
                queued.get("feature_id")
                or manifest.get("feature_id")
                or manifest.get("pdd_id")
                or ""
            ),
            task_map_ref=str(
                queued.get("task_map_ref") or manifest.get("task_map_ref") or ""
            ),
            source_index_ref=str(
                queued.get("source_index_ref")
                or manifest.get("source_index_ref")
                or ""
            ),
            wave=queued.get("wave"),
            causation_id=assigned_event.id,
        )
        if sent:
            dispatched += 1
    return dispatched


def _fanout_terminal(manifest: dict[str, Any]) -> bool:
    terminal_statuses = {"completed", "failed", "timed_out", "cancelled"}
    aggregate = (
        manifest.get("aggregate")
        if isinstance(manifest.get("aggregate"), dict)
        else {}
    )
    return (
        str(manifest.get("status") or "") in terminal_statuses
        or str(aggregate.get("status") or "") in terminal_statuses
    )


def _occupied_lane_ids(manifest: dict[str, Any], *, stage_slot: str) -> set[str]:
    terminal_child_ids = {
        str(child.get("child_id") or "")
        for child in manifest.get("children", []) or []
        if isinstance(child, dict)
        and str(child.get("status") or "") in {"completed", "failed"}
    }
    occupied = {
        str(slot.get("lane_id") or "")
        for slot in manifest.get("slot_state", []) or []
        if isinstance(slot, dict)
        and str(slot.get("stage_slot") or "") == stage_slot
        and str(slot.get("child_id") or "") not in terminal_child_ids
        and str(slot.get("lane_id") or "")
    }
    occupied.update({
        str(child.get("lane_id") or "")
        for child in manifest.get("children", []) or []
        if isinstance(child, dict)
        and str(child.get("status") or "") == "dispatched"
        and str(child.get("stage_slot") or "") == stage_slot
        and str(child.get("lane_id") or "")
    })
    return occupied


def _queue_key(child: dict[str, Any]) -> tuple[int, str]:
    try:
        order = int(child.get("queue_order") or 0)
    except (TypeError, ValueError):
        order = 0
    return order, str(child.get("child_id") or "")


def _queued_child_payload(child: dict[str, Any]) -> dict[str, Any]:
    payload = dict(child.get("payload")) if isinstance(child.get("payload"), dict) else {}
    for key in (
        "task_id",
        "scope",
        "task_map_ref",
        "source_index_ref",
        "blocked_by",
        "depends_on",
    ):
        value = child.get(key)
        if value not in (None, ""):
            payload[key] = value
    return payload


def _newer_writer_replan_fanout(
    orchestrator: Any,
    *,
    fanout_id: str,
    manifest: dict[str, Any],
) -> str:
    """Return the latest higher replan generation in the same run scope."""

    fanout_root = orchestrator.state_dir / "fanouts"
    if not fanout_root.exists():
        return ""
    try:
        events = orchestrator.event_log.read_all()
    except Exception:
        return ""
    started_order = {
        str(event.payload.get("fanout_id") or ""): index
        for index, event in enumerate(events)
        if event.type == "fanout.started" and isinstance(event.payload, dict)
    }
    current_order = started_order.get(fanout_id, -1)
    if current_order < 0:
        return ""
    attempt, scope = _writer_generation_identity(manifest)
    if not any(scope[1:]):
        return ""

    newer: list[tuple[int, int, str]] = []
    for path in fanout_root.glob("*/manifest.json"):
        candidate_id = path.parent.name
        if candidate_id == fanout_id:
            continue
        candidate_order = started_order.get(candidate_id, -1)
        if candidate_order <= current_order:
            continue
        candidate = orchestrator._fanout_manifest(candidate_id)
        if (
            not candidate
            or str(candidate.get("topology") or "") != "fanout_writer_scoped"
        ):
            continue
        candidate_attempt, candidate_scope = _writer_generation_identity(candidate)
        if candidate_attempt <= attempt or candidate_scope != scope:
            continue
        newer.append((candidate_attempt, candidate_order, candidate_id))
    return max(newer, default=(0, -1, ""))[2]


def _writer_generation_identity(
    manifest: dict[str, Any],
) -> tuple[int, tuple[str, str, str, str]]:
    sources: list[dict[str, Any]] = []
    trigger = manifest.get("trigger_payload")
    if isinstance(trigger, dict):
        sources.append(trigger)
    sources.append(manifest)
    for child in manifest.get("children", []) or []:
        if not isinstance(child, dict):
            continue
        payload = child.get("payload")
        if isinstance(payload, dict):
            nested = payload.get("trigger_payload")
            if isinstance(nested, dict):
                sources.append(nested)
            sources.append(payload)
        sources.append(child)

    attempt = _first_int(sources, "rework_attempt")
    workflow_run_id = _first_text(sources, "workflow_run_id", "run_id")
    return attempt, (
        str(manifest.get("stage_id") or ""),
        workflow_run_id
        or str(manifest.get("workflow_run_id") or manifest.get("trace_id") or ""),
        str(manifest.get("pdd_id") or ""),
        str(manifest.get("feature_id") or ""),
    )


def _first_int(sources: list[dict[str, Any]], key: str) -> int:
    for source in sources:
        value = source.get(key)
        if value in (None, ""):
            continue
        try:
            return int(value)
        except (TypeError, ValueError):
            continue
    return 0


def _first_text(sources: list[dict[str, Any]], *keys: str) -> str:
    for source in sources:
        for key in keys:
            value = str(source.get(key) or "").strip()
            if value:
                return value
    return ""


__all__ = [
    "reconcile_active_affinity_writer_fanouts",
    "reconcile_affinity_writer_slots",
]
