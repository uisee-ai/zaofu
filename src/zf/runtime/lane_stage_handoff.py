"""Lane-level handoff helpers for per-lane pipeline stage transitions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from zf.core.events.model import ZfEvent

LANE_STAGE_HANDOFF_SUCCESS_EVENT = "lane.stage.completed"
LANE_STAGE_HANDOFF_FAILURE_EVENT = "lane.stage.failed"
LANE_STAGE_REWORK_REQUESTED_EVENT = "lane.stage.rework.requested"
LANE_STAGE_REWORK_QUARANTINED_EVENT = "lane.stage.rework.quarantined"


STAGE_LEVEL_EVENTS: dict[str, tuple[str, str]] = {
    "review": ("review.approved", "review.rejected"),
    "verify": ("test.passed", "test.failed"),
}


@dataclass(frozen=True)
class LaneStageMatch:
    pipeline: Any
    stage_index: int
    stage_slot: str
    next_stage_slot: str


@dataclass(frozen=True)
class LaneStageReadiness:
    ready: bool
    success_event: str
    failure_event: str
    required_task_ids: list[str]
    completed_task_ids: list[str]
    lane_stage_event_ids: list[str]
    stale_task_ids: list[str]
    failed_task_ids: list[str]


def stage_level_pair(stage_slot: str) -> tuple[str, str]:
    return STAGE_LEVEL_EVENTS.get(
        stage_slot,
        (f"{stage_slot}.completed", f"{stage_slot}.failed"),
    )


def per_lane_flow_match(config: Any, stage_id: str, stage_slot: str) -> LaneStageMatch | None:
    workflow = getattr(config, "workflow", None)
    for pipeline in getattr(workflow, "pipelines", []) or []:
        if str(getattr(pipeline, "stage_transition", "") or "") != "per_lane":
            continue
        stages = list(getattr(pipeline, "stages", []) or [])
        for index, stage in enumerate(stages):
            slot = str(getattr(stage, "stage_id", "") or "")
            if slot != stage_slot:
                continue
            materialized_id = f"{getattr(pipeline, 'pipeline_id', '')}-{slot}"
            if stage_id and stage_id != materialized_id:
                continue
            next_slot = ""
            if index + 1 < len(stages):
                next_slot = str(getattr(stages[index + 1], "stage_id", "") or "")
            return LaneStageMatch(
                pipeline=pipeline,
                stage_index=index,
                stage_slot=slot,
                next_stage_slot=next_slot,
            )
    return None


def per_lane_flow_for_handoff_target(config: Any, stage_id: str, next_stage_slot: str) -> Any | None:
    workflow = getattr(config, "workflow", None)
    for pipeline in getattr(workflow, "pipelines", []) or []:
        if str(getattr(pipeline, "stage_transition", "") or "") != "per_lane":
            continue
        materialized_id = f"{getattr(pipeline, 'pipeline_id', '')}-{next_stage_slot}"
        if stage_id == materialized_id:
            return pipeline
    return None


def terminal_stage_slot(pipeline: Any) -> str:
    stages = list(getattr(pipeline, "stages", []) or [])
    if not stages:
        return ""
    return str(getattr(stages[-1], "stage_id", "") or "")


def failure_target_stage_slot(match: LaneStageMatch) -> str:
    stages = list(getattr(match.pipeline, "stages", []) or [])
    if match.stage_index < 0 or match.stage_index >= len(stages):
        return ""
    return str(getattr(stages[match.stage_index], "rework_to", "") or "").strip()


def lane_stage_event_recorded(
    events: list[ZfEvent],
    *,
    event_type: str,
    fanout_id: str,
    child_id: str,
    stage_slot: str,
    source_event_id: str,
) -> bool:
    for event in reversed(events):
        if event.type != event_type:
            continue
        payload = event.payload if isinstance(event.payload, dict) else {}
        if source_event_id and str(payload.get("source_event_id") or "") == source_event_id:
            return True
        if (
            str(payload.get("fanout_id") or "") == fanout_id
            and str(payload.get("child_id") or "") == child_id
            and str(payload.get("stage_slot") or "") == stage_slot
        ):
            return True
    return False


def final_readiness_already_published(
    events: list[ZfEvent],
    *,
    event_type: str,
    pipeline_id: str,
    root_fanout_id: str,
    lane_stage_event_ids: list[str],
) -> bool:
    wanted = sorted(event_id for event_id in lane_stage_event_ids if event_id)
    for event in reversed(events):
        if event.type != event_type:
            continue
        payload = event.payload if isinstance(event.payload, dict) else {}
        if str(payload.get("pipeline_id") or "") != pipeline_id:
            continue
        if str(payload.get("root_fanout_id") or "") != root_fanout_id:
            continue
        existing = payload.get("lane_stage_event_ids")
        if isinstance(existing, list) and sorted(str(item) for item in existing) == wanted:
            return True
    return False


def lane_stage_rework_already_requested(
    events: list[ZfEvent],
    *,
    lane_stage_event_id: str,
) -> bool:
    if not lane_stage_event_id:
        return False
    for event in reversed(events):
        if event.type not in {
            LANE_STAGE_REWORK_REQUESTED_EVENT,
            LANE_STAGE_REWORK_QUARANTINED_EVENT,
        }:
            continue
        payload = event.payload if isinstance(event.payload, dict) else {}
        if str(payload.get("lane_stage_event_id") or "") == lane_stage_event_id:
            return True
    return False


def lane_stage_rework_attempt_count(
    events: list[ZfEvent],
    *,
    pipeline_id: str,
    root_fanout_id: str,
    task_id: str,
    lane_id: str,
    failed_stage_slot: str,
    target_stage_slot: str,
) -> int:
    count = 0
    for event in events:
        if event.type != LANE_STAGE_REWORK_REQUESTED_EVENT:
            continue
        payload = event.payload if isinstance(event.payload, dict) else {}
        if (
            str(payload.get("pipeline_id") or "") == pipeline_id
            and str(payload.get("root_fanout_id") or "") == root_fanout_id
            and str(payload.get("task_id") or "") == task_id
            and str(payload.get("lane_id") or "") == lane_id
            and str(payload.get("failed_stage_slot") or "") == failed_stage_slot
            and str(payload.get("target_stage_slot") or "") == target_stage_slot
        ):
            count += 1
    return count


def evaluate_final_readiness(
    events: list[ZfEvent],
    *,
    pipeline: Any,
    root_fanout_id: str,
    required_task_ids: list[str],
) -> LaneStageReadiness:
    last_slot = terminal_stage_slot(pipeline)
    success_event, failure_event = stage_level_pair(last_slot)
    pipeline_id = str(getattr(pipeline, "pipeline_id", "") or "")
    latest_last_by_task: dict[str, tuple[int, ZfEvent]] = {}
    latest_stage_by_task: dict[str, tuple[int, ZfEvent]] = {}
    latest_rework_by_task: dict[str, tuple[int, ZfEvent]] = {}
    for index, event in enumerate(events):
        if event.type == LANE_STAGE_REWORK_REQUESTED_EVENT:
            payload = event.payload if isinstance(event.payload, dict) else {}
            if (
                str(payload.get("pipeline_id") or "") == pipeline_id
                and str(payload.get("root_fanout_id") or "") == root_fanout_id
            ):
                task_id = str(payload.get("task_id") or event.task_id or "")
                if task_id:
                    latest_rework_by_task[task_id] = (index, event)
            continue
        if event.type not in {
            LANE_STAGE_HANDOFF_SUCCESS_EVENT,
            LANE_STAGE_HANDOFF_FAILURE_EVENT,
        }:
            continue
        payload = event.payload if isinstance(event.payload, dict) else {}
        if str(payload.get("pipeline_id") or "") != pipeline_id:
            continue
        if str(payload.get("root_fanout_id") or "") != root_fanout_id:
            continue
        task_id = str(payload.get("task_id") or event.task_id or "")
        if not task_id:
            continue
        latest_stage_by_task[task_id] = (index, event)
        if str(payload.get("stage_slot") or "") == last_slot:
            latest_last_by_task[task_id] = (index, event)

    completed_task_ids: list[str] = []
    lane_stage_event_ids: list[str] = []
    stale_task_ids: list[str] = []
    failed_task_ids: list[str] = []
    for task_id in required_task_ids:
        last_entry = latest_last_by_task.get(task_id)
        if last_entry is None:
            continue
        last_index, last_event = last_entry
        last_payload = (
            last_event.payload if isinstance(last_event.payload, dict) else {}
        )
        newest_stage = latest_stage_by_task.get(task_id)
        if newest_stage is not None and newest_stage[0] > last_index:
            stale_task_ids.append(task_id)
            continue
        if last_event.type == LANE_STAGE_HANDOFF_FAILURE_EVENT:
            rework_entry = latest_rework_by_task.get(task_id)
            if rework_entry is not None and rework_entry[0] > last_index:
                rework_payload = (
                    rework_entry[1].payload
                    if isinstance(rework_entry[1].payload, dict)
                    else {}
                )
                if str(rework_payload.get("lane_stage_event_id") or "") in {
                    "",
                    last_event.id,
                }:
                    stale_task_ids.append(task_id)
                    continue
            failed_task_ids.append(task_id)
            lane_stage_event_ids.append(last_event.id)
            continue
        if str(last_payload.get("status") or "completed") != "completed":
            failed_task_ids.append(task_id)
            lane_stage_event_ids.append(last_event.id)
            continue
        completed_task_ids.append(task_id)
        lane_stage_event_ids.append(last_event.id)

    ready = (
        bool(required_task_ids)
        and set(completed_task_ids) == set(required_task_ids)
        and not stale_task_ids
        and not failed_task_ids
    )
    return LaneStageReadiness(
        ready=ready,
        success_event=success_event,
        failure_event=failure_event,
        required_task_ids=list(required_task_ids),
        completed_task_ids=completed_task_ids,
        lane_stage_event_ids=lane_stage_event_ids,
        stale_task_ids=stale_task_ids,
        failed_task_ids=failed_task_ids,
    )
