"""lane_pipeline — doc 88 高层 workflow primitive 的 inspect-only 编译器。

P0 范围(backlog 2026-06-11-0327):解析 `workflow.pipelines[].kind:
lane_pipeline`、编译为 doc 89 §2 输出合同、产出 fail-closed STOP 诊断、
供 `zf workflow inspect` 展示。**不接 runtime dispatch**——本模块不得被
src/zf/runtime/ 导入(测试有 rg 证明);它只生成 desired topology,
恢复决策属 doc 87 reconciler(doc 88 rev1 §6.4 禁止 lane-local recovery)。

rev1 语义已编入合同:lane 任意终态(verified/blocked/superseded)释放、
attempt 绑定唯一推导(歧义即 stale)、trace 级统一 attempt 预算
(默认 Σ per-stage caps,G1 加性封顶)。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


class LanePipelineSpecError(ValueError):
    """Spec 解析失败(未知键/缺必填/类型错)——loader 包装为 ConfigError。"""


_KNOWN_PIPELINE_KEYS = frozenset({
    "id", "kind", "trigger", "task_source", "affinity_key", "lane_count",
    "overflow", "max_rework_attempts", "trace_budget",
    "require_artifact_digests", "stages", "final", "barriers",
    "lane_role_template", "schema_profile", "schema_overrides",
    "assembly", "instruction_refs",
})
_KNOWN_TASK_SOURCE_KEYS = frozenset({"task_map_ref"})
_KNOWN_STAGE_KEYS = frozenset({
    "id", "role_pattern", "terminal", "deadline_seconds", "on_failure",
})
_KNOWN_TERMINAL_KEYS = frozenset({"success", "failure"})
_KNOWN_ON_FAILURE_KEYS = frozenset({"rework_to", "feedback_artifact"})
_KNOWN_FINAL_KEYS = frozenset({"when", "role", "success", "failure"})
_KNOWN_BARRIER_KEYS = frozenset({"stage_transition", "final"})

_VALID_OVERFLOW = frozenset({"first_released_lane", "none"})
_VALID_FINAL_WHEN = frozenset({"all_tasks_verified"})
_VALID_STAGE_TRANSITION = frozenset({"stage_barrier", "per_lane"})
_VALID_FINAL_BARRIER = frozenset({"all_lanes_verified"})
# doc 88 rev1 §4.2: lane 在任意终态释放,blocked 不钉死车道。
LANE_RELEASE_TERMINALS = ("verified", "blocked", "superseded")
LANE_STAGE_HANDOFF_SUCCESS_EVENT = "lane.stage.completed"
LANE_STAGE_HANDOFF_FAILURE_EVENT = "lane.stage.failed"
LANE_STAGE_HANDOFF_IDENTITY_FIELDS = (
    "pipeline_id", "task_id", "attempt_id", "stage_slot", "lane_id",
)


def _reject_unknown(raw: dict, known: frozenset[str], context: str) -> None:
    unknown = sorted(str(k) for k in raw if str(k) not in known)
    if unknown:
        raise LanePipelineSpecError(
            f"lane_pipeline {context}: unknown key(s) {unknown}; "
            f"typo'd keys must fail closed (doc 88 P0)"
        )


@dataclass(frozen=True)
class LaneStageSpec:
    stage_id: str
    role_pattern: str
    success_event: str
    failure_event: str
    deadline_seconds: int = 0
    rework_to: str = ""
    feedback_artifact: str = ""


@dataclass(frozen=True)
class LanePipelineSpec:
    pipeline_id: str
    trigger: str
    task_map_ref: str
    affinity_key: str
    lane_count: int
    overflow: str
    max_rework_attempts: int
    trace_budget: int
    require_artifact_digests: bool
    stages: tuple[LaneStageSpec, ...]
    final_when: str
    final_role: str
    final_success: str
    final_failure: str
    stage_transition: str = "stage_barrier"
    final_barrier: str = ""
    lane_role_template: Any | None = None  # LaneRoleTemplateSpec(A1)
    schema_profile: str = ""                       # A2
    schema_overrides: dict = field(default_factory=dict)  # A2
    instruction_refs: dict = field(default_factory=dict)  # 0722(§6.1)
    assembly_task: str = ""    # A6: 组装根 owner task id
    assembly_none: bool = False  # A6: 显式自担(跳过组装校验)
    assembly_declared: bool = False


def parse_lane_pipeline(raw: dict) -> LanePipelineSpec:
    if not isinstance(raw, dict):
        raise LanePipelineSpecError("pipeline entry must be a mapping")
    _reject_unknown(raw, _KNOWN_PIPELINE_KEYS, f"pipeline {raw.get('id')!r}")
    kind = str(raw.get("kind") or "")
    if kind != "lane_pipeline":
        raise LanePipelineSpecError(
            f"unsupported pipeline kind {kind!r} (only lane_pipeline)"
        )
    pipeline_id = str(raw.get("id") or "").strip()
    if not pipeline_id:
        raise LanePipelineSpecError("pipeline.id is required")

    task_source = raw.get("task_source") or {}
    if not isinstance(task_source, dict):
        raise LanePipelineSpecError(f"{pipeline_id}: task_source must be a mapping")
    _reject_unknown(task_source, _KNOWN_TASK_SOURCE_KEYS, f"{pipeline_id}.task_source")

    stages_raw = raw.get("stages") or []
    if not isinstance(stages_raw, list) or not stages_raw:
        raise LanePipelineSpecError(f"{pipeline_id}: stages must be a non-empty list")
    stages: list[LaneStageSpec] = []
    for i, stage_raw in enumerate(stages_raw):
        if not isinstance(stage_raw, dict):
            raise LanePipelineSpecError(f"{pipeline_id}.stages[{i}]: must be a mapping")
        _reject_unknown(stage_raw, _KNOWN_STAGE_KEYS, f"{pipeline_id}.stages[{i}]")
        terminal = stage_raw.get("terminal") or {}
        if not isinstance(terminal, dict):
            raise LanePipelineSpecError(
                f"{pipeline_id}.stages[{i}].terminal must be a mapping"
            )
        _reject_unknown(terminal, _KNOWN_TERMINAL_KEYS, f"{pipeline_id}.stages[{i}].terminal")
        on_failure = stage_raw.get("on_failure") or {}
        if not isinstance(on_failure, dict):
            raise LanePipelineSpecError(
                f"{pipeline_id}.stages[{i}].on_failure must be a mapping"
            )
        _reject_unknown(on_failure, _KNOWN_ON_FAILURE_KEYS, f"{pipeline_id}.stages[{i}].on_failure")
        stage_id = str(stage_raw.get("id") or "").strip()
        # A3 defaulting 约定:terminal 缺省铸造 {stage}.child.completed/failed;
        # 显式 terminal: 为逃生门(cj-min impl 沿用历史名 dev.build.done)。
        success_event = str(terminal.get("success") or "").strip()
        failure_event = str(terminal.get("failure") or "").strip()
        if stage_id and not success_event:
            success_event = f"{stage_id}.child.completed"
        if stage_id and not failure_event:
            failure_event = f"{stage_id}.child.failed"
        # A3/A4:role_pattern 约定缺省 {stage}-lane-{lane}(parse 层,
        # 生成器与 compiler 共见;显式 pattern 为历史命名逃生门)。
        role_pattern = str(stage_raw.get("role_pattern") or "").strip()
        if stage_id and not role_pattern:
            role_pattern = f"{stage_id}-lane-{{lane}}"
        stages.append(LaneStageSpec(
            stage_id=stage_id,
            role_pattern=role_pattern,
            success_event=success_event,
            failure_event=failure_event,
            deadline_seconds=int(stage_raw.get("deadline_seconds") or 0),
            rework_to=str(on_failure.get("rework_to") or "").strip(),
            feedback_artifact=str(on_failure.get("feedback_artifact") or "").strip(),
        ))

    # A6(doc 90 §3.3.1 / doc 88 §3.2 V1 教训):assembly 声明位。
    assembly_raw = raw.get("assembly")
    assembly_task, assembly_none, assembly_declared = "", False, False
    if assembly_raw is not None:
        assembly_declared = True
        if isinstance(assembly_raw, str):
            if assembly_raw.strip() != "none":
                raise LanePipelineSpecError(
                    f"{pipeline_id}: assembly must be {{task: <id>}} or "
                    f"the literal string 'none'"
                )
            assembly_none = True
        elif isinstance(assembly_raw, dict):
            _reject_unknown(
                assembly_raw, frozenset({"task"}), f"{pipeline_id}.assembly",
            )
            assembly_task = str(assembly_raw.get("task") or "").strip()
            if not assembly_task:
                raise LanePipelineSpecError(
                    f"{pipeline_id}.assembly.task must be a non-empty task id"
                )
        else:
            raise LanePipelineSpecError(
                f"{pipeline_id}: assembly must be a mapping or 'none'"
            )

    final = raw.get("final") or {}
    if not isinstance(final, dict):
        raise LanePipelineSpecError(f"{pipeline_id}: final must be a mapping")
    _reject_unknown(final, _KNOWN_FINAL_KEYS, f"{pipeline_id}.final")

    barriers = raw.get("barriers") or {}
    if not isinstance(barriers, dict):
        raise LanePipelineSpecError(f"{pipeline_id}: barriers must be a mapping")
    _reject_unknown(barriers, _KNOWN_BARRIER_KEYS, f"{pipeline_id}.barriers")
    stage_transition = str(
        barriers.get("stage_transition") or "stage_barrier",
    ).strip()
    final_barrier = str(barriers.get("final") or "").strip()
    if stage_transition == "per_lane" and not final_barrier:
        final_barrier = "all_lanes_verified"

    from zf.core.workflow.lane_role_template import (
        LaneRoleTemplateError,
        parse_lane_role_template,
    )
    try:
        template = parse_lane_role_template(
            raw.get("lane_role_template"), context=pipeline_id,
        )
    except LaneRoleTemplateError as exc:
        raise LanePipelineSpecError(str(exc))

    return LanePipelineSpec(
        pipeline_id=pipeline_id,
        trigger=str(raw.get("trigger") or "").strip(),
        task_map_ref=str(task_source.get("task_map_ref") or "").strip(),
        affinity_key=str(raw.get("affinity_key") or "").strip(),
        lane_count=int(raw.get("lane_count") or 0),
        overflow=str(raw.get("overflow") or "first_released_lane").strip(),
        max_rework_attempts=int(raw.get("max_rework_attempts") or 2),
        trace_budget=int(raw.get("trace_budget") or 0),
        require_artifact_digests=bool(raw.get("require_artifact_digests", False)),
        stages=tuple(stages),
        final_when=str(final.get("when") or "").strip(),
        final_role=str(final.get("role") or "").strip(),
        final_success=str(final.get("success") or "").strip(),
        final_failure=str(final.get("failure") or "").strip(),
        stage_transition=stage_transition,
        final_barrier=final_barrier,
        lane_role_template=template,
        schema_profile=str(raw.get("schema_profile") or "").strip(),
        schema_overrides=(
            raw.get("schema_overrides")
            if isinstance(raw.get("schema_overrides"), dict) else {}
        ),
        assembly_task=assembly_task,
        assembly_none=assembly_none,
        assembly_declared=assembly_declared,
        instruction_refs=(
            {str(k): str(v) for k, v in raw.get("instruction_refs").items()}
            if isinstance(raw.get("instruction_refs"), dict) else {}
        ),
    )


def parse_workflow_pipelines(raw_list: Any) -> list[LanePipelineSpec]:
    if not raw_list:
        return []
    if not isinstance(raw_list, list):
        raise LanePipelineSpecError("workflow.pipelines must be a list")
    return [parse_lane_pipeline(item) for item in raw_list]


def lane_pipeline_rework_events(pipelines: Any) -> frozenset[str]:
    """Failure events routed by lane_pipeline ``on_failure.rework_to``."""
    events: set[str] = set()
    for spec in list(pipelines or []):
        for stage in list(getattr(spec, "stages", ()) or ()):
            failure_event = str(getattr(stage, "failure_event", "") or "").strip()
            rework_to = str(getattr(stage, "rework_to", "") or "").strip()
            if failure_event and rework_to:
                events.add(failure_event)
    return frozenset(events)


# ------------------------------------------------------------------ compile


def _stop(kind: str, message: str) -> dict[str, Any]:
    return {"kind": kind, "severity": "STOP", "message": message}


def compile_lane_pipeline(
    spec: LanePipelineSpec,
    roles: list[Any],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """编译为 doc 89 §2 输出合同 + fail-closed 诊断。

    诊断 severity=STOP 即 inspect 失败(`zf start` 前必须修)——这正是
    G6 的要害:让闭合在启动前可证,`--skip-workflow-inspect` 失去理由。
    """
    diagnostics: list[dict[str, Any]] = []
    known_instances = {
        str(getattr(role, "instance_id", "") or getattr(role, "name", ""))
        for role in roles
    }

    if not spec.trigger:
        diagnostics.append(_stop(
            "lane_pipeline_missing_trigger",
            f"{spec.pipeline_id}: trigger is required",
        ))
    if not spec.affinity_key:
        diagnostics.append(_stop(
            "lane_pipeline_missing_affinity_key",
            f"{spec.pipeline_id}: affinity_key is required "
            f"(task_map items route to lanes by this key)",
        ))
    if spec.lane_count < 1:
        diagnostics.append(_stop(
            "lane_pipeline_invalid_lane_count",
            f"{spec.pipeline_id}: lane_count must be >= 1",
        ))
    if spec.overflow not in _VALID_OVERFLOW:
        diagnostics.append(_stop(
            "lane_pipeline_unclear_overflow",
            f"{spec.pipeline_id}: overflow {spec.overflow!r} not in "
            f"{sorted(_VALID_OVERFLOW)}",
        ))
    if spec.stage_transition not in _VALID_STAGE_TRANSITION:
        diagnostics.append(_stop(
            "lane_pipeline_invalid_stage_transition",
            f"{spec.pipeline_id}: barriers.stage_transition "
            f"{spec.stage_transition!r} not in "
            f"{sorted(_VALID_STAGE_TRANSITION)}",
        ))
    if spec.final_barrier and spec.final_barrier not in _VALID_FINAL_BARRIER:
        diagnostics.append(_stop(
            "lane_pipeline_invalid_final_barrier",
            f"{spec.pipeline_id}: barriers.final {spec.final_barrier!r} "
            f"not in {sorted(_VALID_FINAL_BARRIER)}",
        ))
    if spec.require_artifact_digests and not spec.task_map_ref:
        diagnostics.append(_stop(
            "lane_pipeline_missing_digest_source",
            f"{spec.pipeline_id}: require_artifact_digests=true but "
            f"task_source.task_map_ref is empty (no digest source)",
        ))
    if spec.final_when and spec.final_when not in _VALID_FINAL_WHEN:
        diagnostics.append(_stop(
            "lane_pipeline_invalid_final_when",
            f"{spec.pipeline_id}: final.when {spec.final_when!r} not in "
            f"{sorted(_VALID_FINAL_WHEN)}",
        ))
    if not spec.assembly_declared:
        diagnostics.append(_stop(
            "lane_pipeline_missing_assembly_decl",
            f"{spec.pipeline_id}: assembly 声明位缺失 —— greenfield 流水线的"
            f"两类无主地带(R21 脚手架无主 / R24 组装无主)必须显式归属:"
            f"assembly: {{task: <id>}} 或 assembly: none(自担后果;"
            f"doc 88 §3.2 / doc 90 §3.3.1)",
        ))
    if spec.final_role and spec.final_role not in known_instances:
        diagnostics.append(_stop(
            "lane_pipeline_final_role_missing",
            f"{spec.pipeline_id}: final.role {spec.final_role!r} is not a "
            f"configured role",
        ))

    stage_ids = [s.stage_id for s in spec.stages]
    compiled_stages: list[dict[str, Any]] = []
    for idx, stage in enumerate(spec.stages):
        if not stage.stage_id:
            diagnostics.append(_stop(
                "lane_pipeline_stage_missing_id",
                f"{spec.pipeline_id}.stages[{idx}]: id is required",
            ))
            continue
        # role_pattern 展开:每条 lane 一个具体 role,缺一个即 STOP。
        lanes: dict[str, str] = {}
        missing_roles: list[str] = []
        if not stage.role_pattern:
            diagnostics.append(_stop(
                "lane_pipeline_role_pattern_missing",
                f"{spec.pipeline_id}.{stage.stage_id}: role_pattern is required",
            ))
        else:
            for lane in range(max(spec.lane_count, 0)):
                try:
                    role_name = stage.role_pattern.format(lane=lane)
                except (KeyError, IndexError, ValueError) as exc:
                    diagnostics.append(_stop(
                        "lane_pipeline_role_pattern_invalid",
                        f"{spec.pipeline_id}.{stage.stage_id}: role_pattern "
                        f"{stage.role_pattern!r} failed to expand: {exc}",
                    ))
                    break
                lanes[f"lane{lane}"] = role_name
                if role_name not in known_instances:
                    missing_roles.append(role_name)
        if missing_roles:
            diagnostics.append(_stop(
                "lane_pipeline_role_missing",
                f"{spec.pipeline_id}.{stage.stage_id}: expanded role(s) not "
                f"configured: {missing_roles}",
            ))
        if not stage.success_event:
            diagnostics.append(_stop(
                "lane_pipeline_missing_terminal",
                f"{spec.pipeline_id}.{stage.stage_id}: terminal.success is "
                f"required (a stage without a terminal cannot close)",
            ))
        # failure 必须有去处:on_failure.rework_to 指向已知 stage,或显式
        # 接受 blocked 终态(无 failure event = 不产失败,豁免)。
        if stage.failure_event and stage.rework_to:
            if stage.rework_to not in stage_ids:
                diagnostics.append(_stop(
                    "lane_pipeline_invalid_rework_target",
                    f"{spec.pipeline_id}.{stage.stage_id}: on_failure."
                    f"rework_to {stage.rework_to!r} is not a pipeline stage "
                    f"(stages: {stage_ids})",
                ))
        elif stage.failure_event and not stage.rework_to and idx > 0:
            diagnostics.append(_stop(
                "lane_pipeline_missing_rework_route",
                f"{spec.pipeline_id}.{stage.stage_id}: failure event "
                f"{stage.failure_event!r} has no on_failure.rework_to and is "
                f"not the entry stage (terminal blocked only allowed at "
                f"entry); declare the rework target explicitly",
            ))
        next_stage = stage_ids[idx + 1] if idx + 1 < len(stage_ids) else ""
        compiled_stage = {
            "stage_id": stage.stage_id,
            "role_selector": {
                "pattern": stage.role_pattern,
                "lanes": lanes,
            },
            "terminal_success_event": stage.success_event,
            "terminal_failure_event": stage.failure_event,
            "deadline": stage.deadline_seconds,
            "max_rearm_attempts": spec.max_rework_attempts,
            "artifact_requirements": (
                {"digest": "required"} if spec.require_artifact_digests else {}
            ),
            "next_stage": next_stage,
            "failure_target": stage.rework_to,
            "feedback_artifact": stage.feedback_artifact,
            "transition_scope": spec.stage_transition,
        }
        if spec.stage_transition == "per_lane":
            compiled_stage["handoff_success_event"] = (
                LANE_STAGE_HANDOFF_SUCCESS_EVENT
            )
            compiled_stage["handoff_failure_event"] = (
                LANE_STAGE_HANDOFF_FAILURE_EVENT
            )
        compiled_stages.append(compiled_stage)

    effective_budget = spec.trace_budget or max(
        1, spec.max_rework_attempts * max(len(spec.stages), 1),
    )
    contract = {
        "schema_version": "lane-pipeline-contract.v1",
        "pipeline_id": spec.pipeline_id,
        "trigger": spec.trigger,
        "task_source": {"task_map_ref": spec.task_map_ref},
        "affinity_key": spec.affinity_key,
        "lane_count": spec.lane_count,
        "overflow_policy": spec.overflow,
        "stage_transition": spec.stage_transition,
        "stages": compiled_stages,
        "final_gate": {
            "when": spec.final_when,
            "role": spec.final_role,
            "success": spec.final_success,
            "failure": spec.final_failure,
            "barrier": spec.final_barrier,
            # doc 88 rev1 M7: blocked task 存在时 final 永不满足;
            # partial-delivery 是 owner 显式语义。
            "blocked_tasks_block_final": True,
        },
        "handoff_contract": _derive_handoff_contract(spec),
        # rev1 语义,reconciler(doc 87)与 runtime(P1+)的共同输入:
        "lane_release_on": list(LANE_RELEASE_TERMINALS),
        "attempt_binding": "unique_derivation_or_stale",
        "trace_budget": effective_budget,
        "recovery_owner": "doc87-reconciler",  # 88 不实现 recovery sweep
        "schema_profile": spec.schema_profile,  # A2(来源映射由 loader 落)
        # V4:预算用大白话进 inspect。
        "budget_plain": (
            f"本 trace 预算 {effective_budget} 次推进机会;"
            f"每 stage 最多 {spec.max_rework_attempts} 次返工,"
            f"超出即 quarantine(doc 87 终态裁决)"
        ),
        "instruction_refs": dict(spec.instruction_refs),  # 引用,非 truth(§6.1)
        "assembly": (
            "none" if spec.assembly_none
            else {"task": spec.assembly_task} if spec.assembly_task
            else None
        ),
        # A3 派生:消除手写 affinity_lanes 表与 rework_routing 条目。
        "affinity_lanes": _derive_affinity_lanes(spec, compiled_stages),
        "rework_routing": _derive_rework_routing(spec),
    }
    return contract, diagnostics


def _derive_handoff_contract(spec: LanePipelineSpec) -> dict[str, Any]:
    """Stage transition 合同。

    `stage_barrier` 保持现状:stage 级 aggregate gate 触发下一 stage。
    `per_lane` 声明 kernel-owned lane stage 事件,供 reconciler/runtime 后续
    接线;worker 仍只发布自身 terminal event,不能自报 lane handoff。
    """
    if spec.stage_transition != "per_lane":
        return {
            "mode": "stage_barrier",
            "emitter": "stage_aggregate",
        }
    return {
        "mode": "per_lane",
        "emitter": "kernel",
        "events": {
            "success": LANE_STAGE_HANDOFF_SUCCESS_EVENT,
            "failure": LANE_STAGE_HANDOFF_FAILURE_EVENT,
        },
        "identity_fields": list(LANE_STAGE_HANDOFF_IDENTITY_FIELDS),
        "currentness_gate": {
            "attempt_binding": "unique_derivation_or_stale",
            "requires_task_map_ref": True,
            "requires_source_commit": True,
            "requires_handoff_ref": True,
        },
        "dispatch": {
            "scope": "same_lane",
            "next_stage_from": "stage.next_stage",
            "release_entry_lane_on_terminal": (
                spec.overflow == "first_released_lane"
            ),
        },
        "final_barrier": spec.final_barrier or "all_lanes_verified",
    }


def _derive_affinity_lanes(
    spec: LanePipelineSpec,
    compiled_stages: list[dict[str, Any]],
) -> dict[str, Any]:
    """lanes × stages × role_pattern 机械展开(== cj-min 手写表形状)。"""
    lanes: list[dict[str, Any]] = []
    for lane in range(max(spec.lane_count, 0)):
        entry: dict[str, Any] = {"id": f"lane{lane}"}
        for stage in compiled_stages:
            role = stage["role_selector"]["lanes"].get(f"lane{lane}")
            if role:
                entry[stage["stage_id"]] = role
        lanes.append(entry)
    return {
        "affinity_key": spec.affinity_key,
        "lanes": lanes,
    }


def _derive_rework_routing(spec: LanePipelineSpec) -> dict[str, str]:
    """failure 事件 → 同 lane rework 目标(role_pattern 表达 lane 绑定)。

    KERNEL_SWEPT_FAILURE_EVENTS 一律跳过(对齐 d9379b8):candidate 级
    失败(review.rejected/test.failed/judge.failed/integration.failed 等)
    的唯一权威是 kernel candidate-rework sweep——派生侧再铸这些 route
    等于把 cj-min 注释地雷的 backstop 加回来,孵化竞争性 replan 死循环。
    """
    from zf.core.workflow.topology import KERNEL_SWEPT_FAILURE_EVENTS

    routing: dict[str, str] = {}
    by_id = {s.stage_id: s for s in spec.stages}
    for stage in spec.stages:
        if not stage.failure_event or not stage.rework_to:
            continue
        if stage.failure_event in KERNEL_SWEPT_FAILURE_EVENTS:
            continue
        target = by_id.get(stage.rework_to)
        if target is None:
            continue  # invalid_rework_target STOP 已另行产出
        pattern = target.role_pattern or f"{target.stage_id}-lane-{{lane}}"
        routing[stage.failure_event] = f"{pattern}@same_lane"
    # G2 范畴化:final_failure 是 stage 级(candidate 级)失败——无论叫
    # 什么名字都归 kernel candidate-rework sweep 兜底(doc 88 M7),
    # 不铸 agent 路由。点名集只用于 child 级事件的撞名守门。
    return routing


def lane_pipeline_inspection(
    pipelines: list[LanePipelineSpec],
    roles: list[Any],
    *,
    role_meta: list[Any] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """inspect 报告段:compiled contracts + 诊断(STOP 即启动前失败)。

    ``role_meta`` 来自 loader 展开时落的
    ``workflow.pipelines_role_meta``(A1)——inspect 不重放生成:
    loader 已把生成 role 合并进 roles,重放会把自己的产物误判为
    override。
    """
    contracts: list[dict[str, Any]] = []
    diagnostics: list[dict[str, Any]] = []
    for spec in pipelines:
        contract, diags = compile_lane_pipeline(spec, roles)
        metas = [
            m for m in (role_meta or [])
            if getattr(m, "pipeline_id", "") == spec.pipeline_id
        ]
        if getattr(spec, "lane_role_template", None) is not None:
            contract["generated_roles"] = [
                {
                    "name": m.name,
                    "stage_id": m.stage_id,
                    "lane": m.lane,
                    "source": m.source,
                    "overridden_fields": list(m.overridden_fields),
                }
                for m in metas
            ]
        contracts.append(contract)
        diagnostics.extend(diags)
    return contracts, diagnostics


# ---------------------------------------------------------- A6 admission


def validate_lane_pipeline_admission(
    spec: LanePipelineSpec,
    task_items: list[dict[str, Any]],
) -> list[str]:
    """task_map 内容校验(admission 期,doc 90 §3.3.1 拆位的后半)。

    返回违规消息列表(空 = 通过)。P0 提供纯函数 + 测试;P1 在
    ``task_map.ready`` ingest(writer_fanout_admission 站点)接线
    fail-closed——内容依赖 ``${task_map_ref}`` 解析,inspect 期通常
    不可用,故不属编译期 STOP。

    校验项(doc 88 §3.2/§5):
    1. 声明的 assembly task 必须存在于 task_map;
    2. 工作区根文件(根级 file/glob 路径)必须有 owner——R21 教训:
       package.json/tsconfig 无主,根 `tsc -b` 从未被任何 lane 执行。
       assembly: none 跳过 (1) 但仍要求 (2)。
    """
    problems: list[str] = []
    ids = {str(item.get("task_id") or "") for item in task_items}
    # B-R28-07 clause 1 (R27 ISSUE-002 / R29): pipeline 声明的字面 assembly id
    # 不在 task_map 时,**也接受角色制** —— 任一任务 root_owner_class=="assembly"
    # 即满足(synth 正确产出 assembly 角色任务但没叫那个字面名,如
    # CJMIN-PI-CORE-001)。字面 id 耦合太脆,role 是真相。
    has_role_assembly = any(
        str(item.get("root_owner_class") or "").strip() == "assembly"
        for item in task_items
    )
    if spec.assembly_task and spec.assembly_task not in ids and not has_role_assembly:
        problems.append(
            f"assembly task {spec.assembly_task!r} declared by pipeline "
            f"{spec.pipeline_id!r} is not present in the task_map "
            f"(and no task has root_owner_class=assembly)"
        )

    # B-R28-07 clause 2 (R29): scaffolding 可被 owner 持在**子目录**(greenfield
    # scaffold 进 cj-min/ 时根文件是 cj-min/package.json,非 repo-root bare)。
    # 原判只认 repo-root 字面路径 → 误判子目录 scaffold「无主」。改为:持
    # root-level 路径,或持任意深度的脚手架文件(package.json/tsconfig 等),
    # 都算 scaffold owner —— R21 保护(scaffolding 必须有主)语义不变,只是
    # 不再对子目录项目假阴性。
    _SCAFFOLD_BASENAMES = {
        "package.json", "pnpm-workspace.yaml", "pnpm-lock.yaml",
        "tsconfig.json", "tsconfig.base.json", "vitest.config.ts",
        "eslint.config.js",
    }

    def _has_root_path(item: dict[str, Any]) -> bool:
        for raw_path in item.get("allowed_paths") or []:
            path = str(raw_path).strip().strip("/")
            if not path:
                continue
            head = path.split("/", 1)[0]
            if "/" not in path or head in ("*", "**"):
                return True
            if path.rsplit("/", 1)[-1] in _SCAFFOLD_BASENAMES:
                return True
        return False

    root_owners = [
        str(item.get("task_id") or "")
        for item in task_items if _has_root_path(item)
    ]
    single_assembly_slice = (
        len(task_items) == 1
        and str(task_items[0].get("root_owner_class") or "").strip() == "assembly"
        and bool(str(task_items[0].get("task_id") or "").strip())
    )
    if not root_owners:
        if not single_assembly_slice:
            problems.append(
                f"no task in the task_map owns workspace-root paths "
                f"(scaffolding such as package.json/tsconfig has no owner — "
                f"the R21 failure shape); give the assembly/scaffold task a "
                f"root-level allowed_path or split one out"
            )
    return problems


# ------------------------------------------------------- 0722 instructionRefs


def instruction_ref_diagnostics(
    spec: LanePipelineSpec,
    *,
    project_root: Any,
    state_dir: Any,
    harness_profile: str = "baseline",
) -> list[dict[str, Any]]:
    """instruction_refs 校验(doc 90 §6.1):repo artifact 引用,非 truth。

    - 路径必须在 repo 内(拒绝绝对路径与 ``..`` 逃逸);
    - 不得指向 runtime state(state_dir)或 .git;
    - 缺失文件 baseline=WARN,strict/release=STOP;
    - 不参与任何状态推进(仅入 compiled contract 作引用)。
    """
    from pathlib import Path

    diagnostics: list[dict[str, Any]] = []
    root = Path(project_root).resolve()
    state = Path(state_dir).resolve()
    missing_severity = (
        "STOP" if harness_profile in ("strict", "release") else "WARN"
    )
    for name, raw_path in (spec.instruction_refs or {}).items():
        path = Path(str(raw_path))
        if path.is_absolute() or ".." in path.parts:
            diagnostics.append(_stop(
                "lane_pipeline_instruction_ref_escape",
                f"{spec.pipeline_id}.instruction_refs[{name}]: path "
                f"{raw_path!r} must be repo-relative without '..'",
            ))
            continue
        resolved = (root / path).resolve()
        if not str(resolved).startswith(str(root)):
            diagnostics.append(_stop(
                "lane_pipeline_instruction_ref_escape",
                f"{spec.pipeline_id}.instruction_refs[{name}]: escapes repo",
            ))
            continue
        if (
            str(resolved).startswith(str(state))
            or ".git" in path.parts
        ):
            diagnostics.append(_stop(
                "lane_pipeline_instruction_ref_runtime_state",
                f"{spec.pipeline_id}.instruction_refs[{name}]: must not "
                f"point into runtime state or .git",
            ))
            continue
        if not resolved.exists():
            diagnostics.append({
                "kind": "lane_pipeline_instruction_ref_missing",
                "severity": missing_severity,
                "message": (
                    f"{spec.pipeline_id}.instruction_refs[{name}]: "
                    f"{raw_path!r} does not exist "
                    f"(baseline WARN, strict/release STOP; doc 90 §6.1)"
                ),
            })
    return diagnostics
