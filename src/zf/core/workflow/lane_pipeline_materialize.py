"""lane_pipeline → canonical stages 物化(doc 88 P1 切片 1,G3)。

与 kind: Workflow 同构:lane_pipeline 在 load 期编译为普通 canonical
stage dict,进既有 `_build_workflow_stages` / doc 74 compiler / runtime
——**不建第二 scheduler,本模块不被 runtime 导入**。

守门(doc 90 §7):手写 stages 已覆盖同一 trigger → 跳过物化并 WARN
(双表示漂移提示)。hermes v1/v2 的双表示现状因此零回归;pipelines-only
配置(v4 形)从本切片起可直接运行。

物化形 = canonical-dag 词汇的 candidate 级链(与 cj-min 手写 4-stage
同形):impl 写者扇出 → 中间 reader 链(stage 级事件按 canonical 映射:
review→approved/rejected,verify→test.passed/failed,其余
{id}.completed/failed)→ final 终审。doc 88 的 lane 级语义(M5/M6 释放
与 attempt 绑定)仍属 contract 层,归 doc 87 reconciler(P2+)。
"""

from __future__ import annotations

from typing import Any

from zf.core.workflow.lane_pipeline import (
    LANE_STAGE_HANDOFF_FAILURE_EVENT,
    LANE_STAGE_HANDOFF_SUCCESS_EVENT,
)

# stage 级(candidate 级)事件的 canonical 词汇映射;不在表内走
# {id}.completed/failed 约定。
# final 段 child 终态约定(judge 自身失败 = kernel sweep/escalate 域,
# 无 agent 返工路由意义;derive_kernel_swept_events 引用)。
FINAL_CHILD_SUCCESS = "judge.child.completed"
FINAL_CHILD_FAILURE = "judge.child.failed"

_STAGE_LEVEL_EVENTS: dict[str, tuple[str, str]] = {
    "review": ("review.approved", "review.rejected"),
    "verify": ("test.passed", "test.failed"),
}


def lane_profile_name(spec: Any) -> str:
    return f"{spec.pipeline_id}-slot"


def _lane_roles(stage: Any, lane_count: int) -> list[str]:
    pattern = stage.role_pattern or f"{stage.stage_id}-lane-{{lane}}"
    return [pattern.format(lane=i) for i in range(max(lane_count, 0))]


def _stage_level_pair(stage_id: str) -> tuple[str, str]:
    return _STAGE_LEVEL_EVENTS.get(
        stage_id, (f"{stage_id}.completed", f"{stage_id}.failed"),
    )


def _stage_backedge(spec: Any, stage: Any) -> dict[str, Any] | None:
    """Materialize lane_pipeline on_failure as a same-lane canonical backedge."""
    if not getattr(stage, "failure_event", ""):
        return None
    rework_to = str(getattr(stage, "rework_to", "") or "").strip()
    if not rework_to:
        return None
    target = {
        item.stage_id: item
        for item in getattr(spec, "stages", []) or []
    }.get(rework_to)
    if target is None:
        return None
    return {
        "event": stage.failure_event,
        "restart_stage": f"{spec.pipeline_id}-{target.stage_id}",
        "target_affinity": "same_lane",
        "max_attempts": int(getattr(spec, "max_rework_attempts", 0) or 0),
        "feedback_artifact": str(getattr(stage, "feedback_artifact", "") or ""),
        "emit": f"{target.stage_id}.rework.requested",
    }


def materialize_lane_pipeline_stages(spec: Any) -> list[dict[str, Any]]:
    """编译 candidate 级链。spec 须至少 1 个 stage(parse 层已保证)。"""
    stages: list[dict[str, Any]] = []
    profile = lane_profile_name(spec)
    retries = max(int(spec.max_rework_attempts or 1) - 1, 0)
    per_lane = str(getattr(spec, "stage_transition", "") or "") == "per_lane"

    first = spec.stages[0]
    impl: dict[str, Any] = {
        "id": f"{spec.pipeline_id}-{first.stage_id}",
        "trigger": spec.trigger,
        "topology": "fanout_writer_scoped",
        "roles": _lane_roles(first, spec.lane_count),
        "synthesize_canonical_tasks": True,
        "fanout": {"assignment": {
            "strategy": "affinity_stage_slots",
            "lane_profile": profile,
            "stage_slot": first.stage_id,
        }},
        "aggregate": {
            "mode": "candidate_integration",
            "success_event": "candidate.ready",
            "failure_event": "integration.failed",
            "max_retries": retries,
        },
    }
    # 约定默认:task_map ref 由触发事件 payload 携带(${task_map_ref}
    # 渲染;task_map.ready schema 强制该字段)——spec 显式值为逃生门。
    impl["source"] = {"task_map": spec.task_map_ref or "${task_map_ref}"}
    if first.deadline_seconds:
        impl["timeout_seconds"] = int(first.deadline_seconds)
    backedge = _stage_backedge(spec, first)
    if backedge is not None:
        impl["on_fail"] = backedge
    stages.append(impl)

    prev_success = "candidate.ready"
    final_trigger = "candidate.ready"
    for stage in spec.stages[1:]:
        stage_success, stage_failure = _stage_level_pair(stage.stage_id)
        success = LANE_STAGE_HANDOFF_SUCCESS_EVENT if per_lane else stage_success
        failure = LANE_STAGE_HANDOFF_FAILURE_EVENT if per_lane else stage_failure
        trigger = LANE_STAGE_HANDOFF_SUCCESS_EVENT if per_lane else prev_success
        entry: dict[str, Any] = {
            "id": f"{spec.pipeline_id}-{stage.stage_id}",
            "trigger": trigger,
            "topology": "fanout_reader",
            "roles": _lane_roles(stage, spec.lane_count),
            "fanout": {"assignment": {
                "strategy": "affinity_stage_slots",
                "lane_profile": profile,
                "stage_slot": stage.stage_id,
            }},
            "aggregate": {
                "mode": "wait_for_all",
                "child_success_event": stage.success_event,
                "child_failure_event": stage.failure_event,
                "success_event": success,
                "failure_event": failure,
                "max_retries": retries,
            },
        }
        if stage.deadline_seconds:
            entry["timeout_seconds"] = int(stage.deadline_seconds)
        backedge = _stage_backedge(spec, stage)
        if backedge is not None:
            entry["on_fail"] = backedge
        stages.append(entry)
        prev_success = success
        final_trigger = stage_success if per_lane else success

    if spec.final_role:
        stages.append({
            "id": f"{spec.pipeline_id}-final",
            "trigger": final_trigger,
            "topology": "fanout_reader",
            "roles": [spec.final_role],
            "aggregate": {
                "mode": "wait_for_all",
                "child_success_event": FINAL_CHILD_SUCCESS,
                "child_failure_event": FINAL_CHILD_FAILURE,
                "success_event": spec.final_success or "judge.passed",
                "failure_event": spec.final_failure or "judge.failed",
                "max_retries": 0,
            },
        })
    return stages


def materialize_affinity_profile(spec: Any) -> dict[str, Any]:
    """affinity_lanes profile 物化(== A3 contract 派生表的 yaml profile 形)。"""
    lanes: list[dict[str, Any]] = []
    for i in range(max(spec.lane_count, 0)):
        entry: dict[str, Any] = {"id": f"lane{i}"}
        for stage in spec.stages:
            pattern = stage.role_pattern or f"{stage.stage_id}-lane-{{lane}}"
            entry[stage.stage_id] = pattern.format(lane=i)
        lanes.append(entry)
    return {
        "affinity_key": spec.affinity_key,
        "queue": {"order": "priority_fifo"},
        "lanes": lanes,
    }
