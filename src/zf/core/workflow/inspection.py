"""Static workflow inspection derived from ``zf.yaml``.

This module is intentionally read-only: it compiles the deterministic workflow
graph, resolves configured role skills, and reports preflight issues before a
run reaches tmux / provider sessions.
"""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any

from zf.core.config.schema import RoleConfig, ZfConfig
from zf.core.skills import build_skill_lock_entries
from zf.core.workflow.graph import FAILURE_SUFFIXES, compile_workflow_graph
from zf.core.workflow.lane_pipeline import lane_pipeline_rework_events
from zf.core.workflow.runner_policy import pure_aggregator_policy_plan
from zf.core.workflow.topology import (
    EXTERNAL_EVENTS,
    KERNEL_SWEPT_FAILURE_EVENTS,
    derive_kernel_swept_events,
)


RESERVED_ROLE_TRIGGERS: frozenset[str] = frozenset({"task.start", "task.resume"})
RESERVED_ROLE_PUBLISHES: frozenset[str] = frozenset({"task.done"})

_GRAPH_DIAGNOSTIC_SEVERITY: dict[str, str] = {
    "trigger_without_producer": "STOP",
    "invalid_rework_target": "STOP",
    "missing_rework_route": "STOP",
    "missing_aggregate_success_event": "STOP",
    "missing_aggregate_failure_event": "STOP",
    "event_without_consumer": "WARN",
}

_STATUS_ORDER = {"GO": 0, "INFO": 1, "WARN": 2, "STOP": 3}


def build_workflow_inspection_report(
    config: ZfConfig,
    *,
    project_root: Path | None = None,
    state_dir: Path | None = None,
) -> dict[str, Any]:
    """Build a deterministic preflight report for one config."""

    project_root = (project_root or Path.cwd()).resolve()
    state_dir = _resolve_state_dir(config, project_root, state_dir)
    graph = compile_workflow_graph(config)

    diagnostics: list[dict[str, Any]] = []
    event_consumers = _event_consumers(config)
    diagnostics.extend(_graph_diagnostics(
        list(graph.diagnostics),
        event_consumers=event_consumers,
    ))
    diagnostics.extend(_reserved_event_diagnostics(config))
    diagnostics.extend(_terminal_policy_diagnostics(config, graph))
    diagnostics.extend(_explicit_rework_route_diagnostics(config, graph))
    diagnostics.extend(_pure_aggregator_policy_diagnostics(config))
    diagnostics.extend(_external_trigger_producer_diagnostics(config))
    lane_contracts: list[dict[str, Any]] = []
    if getattr(config.workflow, "pipelines", None):
        from zf.core.workflow.lane_pipeline import lane_pipeline_inspection
        lane_contracts, lane_diags = lane_pipeline_inspection(
            config.workflow.pipelines, config.roles,
            role_meta=getattr(config.workflow, "pipelines_role_meta", []),
        )
        diagnostics.extend(lane_diags)
        from zf.core.workflow.lane_pipeline import (
            instruction_ref_diagnostics,
        )
        for pipeline_spec in config.workflow.pipelines:
            diagnostics.extend(instruction_ref_diagnostics(
                pipeline_spec,
                project_root=project_root,
                state_dir=state_dir,
                harness_profile=config.workflow.harness_profile,
            ))

    skill_entries, skill_diagnostics = _skill_report(
        config=config,
        project_root=project_root,
        state_dir=state_dir,
    )
    diagnostics.extend(skill_diagnostics)
    diagnostics = _dedupe_diagnostics(diagnostics)

    role_trigger_consumers = _role_trigger_consumers(config)
    report = {
        "schema_version": "workflow-inspection.v1",
        "project": {
            "name": config.project.name,
            "root": str(project_root),
            "state_dir": str(state_dir),
        },
        "status": _status_from_diagnostics(diagnostics),
        "summary": {
            "roles": len(config.roles),
            "stages": len(config.workflow.stages),
            "graph_nodes": len(graph.nodes),
            "graph_edges": len(graph.edges),
            "diagnostics": _diagnostic_counts(diagnostics),
            "multi_consumer_triggers": [
                {
                    "event": event,
                    "consumers": consumers,
                }
                for event, consumers in sorted(role_trigger_consumers.items())
                if len(consumers) > 1 and event not in EXTERNAL_EVENTS
            ],
        },
        "diagnostics": diagnostics,
        "roles": [_role_summary(role) for role in config.roles],
        "stages": [_stage_summary(stage) for stage in config.workflow.stages],
        "affinity_lanes": _affinity_summary(config),
        "lane_pipelines": lane_contracts,
        "schema_sources": getattr(
            config.workflow, "pipelines_schema_sources", {},
        ),
        "handoff": {
            "terminal_policy": graph.terminal_policy.to_dict(),
            "rework_routes": [route.to_dict() for route in graph.rework_routes],
            "event_sets": graph.event_sets.to_dict(),
        },
        "skills": {
            "sources": [
                {"name": source.name, "path": source.path, "mode": source.mode}
                for source in config.skill_sources
            ],
            "enabled": skill_entries,
        },
        "graph": {
            "nodes": len(graph.nodes),
            "edges": len(graph.edges),
            "diagnostics": list(graph.diagnostics),
        },
    }
    return report


def inspection_failed(report: dict[str, Any], *, strict: bool = False) -> bool:
    status = str(report.get("status", "GO") or "GO")
    return status == "STOP" or (strict and status == "WARN")


def _resolve_state_dir(
    config: ZfConfig,
    project_root: Path,
    state_dir: Path | None,
) -> Path:
    if state_dir is None:
        state_dir = Path(config.project.state_dir or ".zf")
    if not state_dir.is_absolute():
        state_dir = project_root / state_dir
    return state_dir.resolve()


def _graph_diagnostics(
    items: list[dict[str, str]],
    *,
    event_consumers: dict[str, list[str]],
) -> list[dict[str, Any]]:
    diagnostics: list[dict[str, Any]] = []
    for item in items:
        kind = item.get("kind", "")
        severity = _GRAPH_DIAGNOSTIC_SEVERITY.get(kind, "WARN")
        if kind == "missing_rework_route" and _has_event_consumer(
            item.get("event", ""),
            event_consumers,
        ):
            severity = "WARN"
        detail = {
            key: value
            for key, value in item.items()
            if key not in {"kind", "stage_id", "event", "field", "role"}
        }
        diagnostics.append(_diag(
            severity=severity,
            kind=kind,
            message=_graph_message(item),
            source="workflow_graph",
            role=item.get("role", ""),
            stage_id=item.get("stage_id", ""),
            event=item.get("event", ""),
            field=item.get("field", ""),
            detail=detail,
        ))
    return diagnostics


def _has_event_consumer(event: str, consumers: dict[str, list[str]]) -> bool:
    parts = [part.strip() for part in event.split(",") if part.strip()]
    if not parts:
        return False
    return all(part in consumers for part in parts)


def _reserved_event_diagnostics(config: ZfConfig) -> list[dict[str, Any]]:
    diagnostics: list[dict[str, Any]] = []
    for role in config.roles:
        role_ref = _role_ref(role)
        for event in role.triggers:
            if event in RESERVED_ROLE_TRIGGERS:
                diagnostics.append(_diag(
                    severity="STOP",
                    kind="role_uses_reserved_trigger",
                    message=(
                        f"role `{role_ref}` 使用保留 trigger `{event}`；"
                        "任务入口应由 kernel 分派事件驱动"
                    ),
                    role=role_ref,
                    event=event,
                ))
        for event in role.publishes:
            if event in RESERVED_ROLE_PUBLISHES:
                diagnostics.append(_diag(
                    severity="STOP",
                    kind="role_publishes_reserved_terminal",
                    message=(
                        f"role `{role_ref}` 发布保留终态事件 `{event}`；"
                        "终态必须由 Layer 1 gate 写入"
                    ),
                    role=role_ref,
                    event=event,
                ))
    return diagnostics


def _terminal_policy_diagnostics(config: ZfConfig, graph: Any) -> list[dict[str, Any]]:
    if not _has_explicit_workflow(config):
        return []
    published = {
        str(event)
        for role in config.roles
        for event in list(getattr(role, "publishes", []) or [])
    }
    # Fanout stage aggregates emit their success/failure events at runtime
    # (e.g. judge stage aggregate -> judge.passed/judge.failed); count them
    # as producers or every aggregate-terminal config false-STOPs here.
    for stage in list(getattr(config.workflow, "stages", []) or []):
        aggregate = getattr(stage, "aggregate", None)
        for field in ("success_event", "failure_event"):
            value = str(getattr(aggregate, field, "") or "")
            if value:
                published.add(value)
    diagnostics: list[dict[str, Any]] = []
    for event in sorted(
        set(graph.terminal_policy.success_events)
        | set(graph.terminal_policy.failure_events)
    ):
        if event in published or event in EXTERNAL_EVENTS:
            continue
        diagnostics.append(_diag(
            severity="STOP",
            kind="terminal_event_without_producer",
            message=f"终态事件 `{event}` 没有任何 role 发布，任务无法自然结束",
            event=event,
        ))
    return diagnostics


def _has_explicit_workflow(config: ZfConfig) -> bool:
    if config.workflow.stages:
        return True
    return any(role.triggers or role.publishes for role in config.roles)


def _explicit_rework_route_diagnostics(
    config: ZfConfig,
    graph: Any,
) -> list[dict[str, Any]]:
    route_events = {
        str(route.event)
        for route in list(getattr(graph, "rework_routes", ()) or ())
        if str(route.event)
    } | set(lane_pipeline_rework_events(getattr(config.workflow, "pipelines", ())))
    terminal_failures = set(getattr(graph.terminal_policy, "failure_events", frozenset()))
    consumers = _event_consumers(config)
    diagnostics: list[dict[str, Any]] = []
    for event, producer in _failure_event_producers(config):
        if event in route_events:
            continue
        if event in derive_kernel_swept_events(
            getattr(config.workflow, "stages", None),
            getattr(config.workflow, "pipelines", ()),
        ):
            diagnostics.append(_diag(
                severity="INFO",
                kind="kernel_swept_failure_event",
                message=(
                    f"失败事件 `{event}` 由 kernel candidate-rework sweep 兜底"
                    f"(doc 79;显式 routing 曾导致竞争 re-plan 死循环,勿配置)"
                ),
                event=event,
                role=producer,
            ))
            continue
        if event in terminal_failures:
            continue
        if event in consumers:
            diagnostics.append(_diag(
                severity="WARN",
                kind="failure_event_without_explicit_rework_route",
                message=(
                    f"失败事件 `{event}` 没有配置 `workflow.rework_routing`，"
                    f"但会唤醒 {', '.join(consumers[event])}"
                ),
                event=event,
                role=producer,
                detail={"consumers": consumers[event]},
            ))
            continue
        diagnostics.append(_diag(
            severity="STOP",
            kind="explicit_rework_route_missing",
            message=f"失败事件 `{event}` 没有配置 `workflow.rework_routing` 回流目标",
            event=event,
            role=producer,
        ))
    return diagnostics


# Events the kernel deterministically produces OUTSIDE any stage success_event,
# so they may legitimately be declared external_triggers: task_map.ready is
# emitted by the refactor plan→task_map bridge (P0-2 fix) and the candidate
# rework sweep. Extend this set when a new deterministic kernel producer lands.
_KERNEL_PRODUCED_EXTERNAL_TRIGGERS = frozenset({"task_map.ready"})
_WRITER_TOPOLOGY_TOKENS = ("writer", "lane_pipeline")


def _external_trigger_producer_diagnostics(config: ZfConfig) -> list[dict[str, Any]]:
    """P0-2 class: an event declared as an external_trigger that drives a
    writer/impl stage but is produced by no stage success_event (and has no
    deterministic kernel producer) has a broken upstream handoff — the
    refactor plan→task_map.ready livelock was exactly this. Entry triggers
    (user.message, *.scan.requested) drive reader stages and are left alone."""
    dag = getattr(config.workflow, "dag", None)
    external = {str(e) for e in (getattr(dag, "external_triggers", None) or [])}
    if not external:
        return []
    stages = list(getattr(config.workflow, "stages", []) or [])
    produced: set[str] = set()
    for stage in stages:
        agg = getattr(stage, "aggregate", None)
        for ev in (getattr(agg, "success_event", ""), getattr(agg, "failure_event", "")):
            if ev:
                produced.add(str(ev))
    lane_triggers = {
        str(getattr(p, "trigger", "") or "")
        for p in (getattr(config.workflow, "pipelines", None) or [])
    }
    diagnostics: list[dict[str, Any]] = []
    for ev in sorted(external):
        if ev in _KERNEL_PRODUCED_EXTERNAL_TRIGGERS or ev in produced:
            continue
        drives_writer = ev in lane_triggers or any(
            str(getattr(stage, "trigger", "")) == ev
            and any(tok in str(getattr(stage, "topology", ""))
                    for tok in _WRITER_TOPOLOGY_TOKENS)
            for stage in stages
        )
        if drives_writer:
            diagnostics.append(_diag(
                severity="WARN",
                kind="external_trigger_without_producer",
                message=(
                    f"`{ev}` 被列为 external_trigger 且驱动 writer/impl 阶段，但没有任何 stage "
                    "produces 它、kernel 也无确定性 producer —— 上游 handoff 可能无 owner(P0-2 类，"
                    "refactor plan→task_map.ready livelock 就是此形)。确认由 operator 注入，"
                    "或补确定性 kernel producer 并加入 _KERNEL_PRODUCED_EXTERNAL_TRIGGERS。"
                ),
                event=ev,
            ))
    return diagnostics


def _pure_aggregator_policy_diagnostics(config: ZfConfig) -> list[dict[str, Any]]:
    diagnostics: list[dict[str, Any]] = []
    for role in config.roles:
        plan = pure_aggregator_policy_plan(config, role)
        if not plan.get("applies"):
            continue
        role_ref = _role_ref(role)
        if role.role_kind == "writer":
            diagnostics.append(_diag(
                severity="STOP",
                kind="pure_aggregator_role_is_writer",
                message=(
                    f"fanout synth role `{role_ref}` 是 writer；"
                    "synth/reducer 必须是 reader 纯聚合角色"
                ),
                role=role_ref,
                detail={"policy_id": plan.get("policy_id", "")},
            ))
            continue
        if plan.get("applied"):
            diagnostics.append(_diag(
                severity="WARN",
                kind="pure_aggregator_runner_policy_applied",
                message=(
                    f"fanout synth role `{role_ref}` 将在 runner spawn 时应用 "
                    "pure_aggregator 权限收窄"
                ),
                role=role_ref,
                detail={
                    "policy_id": plan.get("policy_id", ""),
                    "backend": plan.get("backend", ""),
                    "changes": dict(plan.get("changes") or {}),
                    "effective": dict(plan.get("effective") or {}),
                },
            ))
    return diagnostics


def _skill_report(
    *,
    config: ZfConfig,
    project_root: Path,
    state_dir: Path,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    entries: list[dict[str, Any]] = []
    diagnostics: list[dict[str, Any]] = []
    for role in config.roles:
        for entry in build_skill_lock_entries(
            project_root=project_root,
            state_dir=state_dir,
            role=role,
            config=config,
        ):
            item = asdict(entry)
            entries.append(item)
            role_ref = item.get("instance_id") or item.get("role") or ""
            if item.get("status") in {"missing", "invalid"}:
                diagnostics.append(_diag(
                    severity="STOP",
                    kind="skill_resolution_failed",
                    message=(
                        f"role `{role_ref}` 启用的 skill `{item.get('name', '')}` "
                        f"解析状态为 `{item.get('status', '')}`"
                    ),
                    role=str(role_ref),
                    field=str(item.get("name", "")),
                ))
            if item.get("collision_candidates"):
                diagnostics.append(_diag(
                    severity="WARN",
                    kind="skill_source_collision",
                    message=(
                        f"role `{role_ref}` 的 skill `{item.get('name', '')}` "
                        "存在多个候选来源，需要确认覆盖顺序"
                    ),
                    role=str(role_ref),
                    field=str(item.get("name", "")),
                    detail={"candidates": list(item.get("collision_candidates", []) or [])},
                ))
            if item.get("warnings"):
                diagnostics.append(_diag(
                    severity="WARN",
                    kind="skill_metadata_warning",
                    message=(
                        f"role `{role_ref}` 的 skill `{item.get('name', '')}` "
                        "metadata 不完整"
                    ),
                    role=str(role_ref),
                    field=str(item.get("name", "")),
                    detail={"warnings": list(item.get("warnings", []) or [])},
                ))
            if item.get("routing_warnings"):
                diagnostics.append(_diag(
                    severity="WARN",
                    kind="skill_routing_warning",
                    message=(
                        f"role `{role_ref}` 的 skill `{item.get('name', '')}` "
                        "与 stage / role / backend visibility 不完全匹配"
                    ),
                    role=str(role_ref),
                    field=str(item.get("name", "")),
                    detail={
                        "warnings": list(item.get("routing_warnings", []) or []),
                        "stages": list(item.get("stages", []) or []),
                        "roles": list(item.get("roles", []) or []),
                        "backends": list(item.get("backends", []) or []),
                        "tags": list(item.get("tags", []) or []),
                        "auto_inject": bool(item.get("auto_inject", False)),
                        "load_on_demand": bool(item.get("load_on_demand", True)),
                    },
                ))
    diagnostics.extend(_duplicate_skill_owner_diagnostics(config))
    return entries, diagnostics


def _duplicate_skill_owner_diagnostics(config: ZfConfig) -> list[dict[str, Any]]:
    owners_by_skill: dict[str, dict[str, RoleConfig]] = {}
    for role in config.roles:
        for skill in set(role.skills):
            owners_by_skill.setdefault(skill, {})[role.name] = role
    diagnostics: list[dict[str, Any]] = []
    for skill, owners in sorted(owners_by_skill.items()):
        roles = list(owners.values())
        verification_owners = [
            role
            for role in roles
            if _verification_like_role(role)
        ]
        if len(verification_owners) < 2:
            continue
        diagnostics.append(_diag(
            severity="WARN",
            kind="skill_duplicate_verification_owner",
            message=(
                f"skill `{skill}` 同时挂在多个 review/test/verify/judge owner 上，"
                "建议保留一个明确 owner 或拆分 stage-specific skill"
            ),
            field=skill,
            detail={
                "roles": [
                    {
                        "name": role.name,
                        "instance_id": role.instance_id or role.name,
                        "backend": role.backend,
                        "stages": list(role.stages),
                    }
                    for role in verification_owners
                ],
            },
        ))
    return diagnostics


def _verification_like_role(role: RoleConfig) -> bool:
    haystack = " ".join([role.name, role.instance_id or "", *role.stages]).lower()
    return any(token in haystack for token in ("review", "verify", "test", "judge", "qa"))


def _role_summary(role: RoleConfig) -> dict[str, Any]:
    return {
        "name": role.name,
        "instance_id": role.instance_id or role.name,
        "backend": role.backend,
        "role_kind": role.role_kind,
        "stages": list(role.stages),
        "triggers": list(role.triggers),
        "publishes": list(role.publishes),
        "skills": list(role.skills),
    }


def _stage_summary(stage: Any) -> dict[str, Any]:
    aggregate = getattr(stage, "aggregate", None)
    assignment = getattr(stage, "assignment", None)
    return {
        "id": str(getattr(stage, "id", "") or ""),
        "trigger": str(getattr(stage, "trigger", "") or ""),
        "topology": str(getattr(stage, "topology", "") or ""),
        "roles": [str(role) for role in list(getattr(stage, "roles", []) or [])],
        "target_ref": str(getattr(stage, "target_ref", "") or ""),
        "task_map": str(getattr(stage, "task_map", "") or ""),
        "success_event": str(getattr(aggregate, "success_event", "") or ""),
        "failure_event": str(getattr(aggregate, "failure_event", "") or ""),
        "child_success_event": str(getattr(aggregate, "child_success_event", "") or ""),
        "child_failure_event": str(getattr(aggregate, "child_failure_event", "") or ""),
        "assignment": {
            "strategy": str(getattr(assignment, "strategy", "") or ""),
            "role_pool": [
                str(role) for role in list(getattr(assignment, "role_pool", []) or [])
            ],
            "lane_profile": str(getattr(assignment, "lane_profile", "") or ""),
            "stage_slot": str(getattr(assignment, "stage_slot", "") or ""),
        },
    }


def _affinity_summary(config: ZfConfig) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for profile_name, profile in sorted(config.workflow.affinity_lanes.items()):
        for lane in profile.lanes:
            rows.append({
                "profile": str(profile_name),
                "affinity_key": str(profile.affinity_key),
                "queue_order": str(profile.queue.order),
                "id": str(lane.id),
                "impl": str(lane.impl),
                "review": str(lane.review),
                "verify": str(lane.verify),
            })
    return rows


def _role_trigger_consumers(config: ZfConfig) -> dict[str, list[str]]:
    consumers: dict[str, list[str]] = {}
    for role in config.roles:
        role_ref = _role_ref(role)
        for event in role.triggers:
            consumers.setdefault(str(event), []).append(role_ref)
    return consumers


def _event_consumers(config: ZfConfig) -> dict[str, list[str]]:
    consumers = _role_trigger_consumers(config)
    for stage in config.workflow.stages:
        trigger = str(getattr(stage, "trigger", "") or "")
        stage_id = str(getattr(stage, "id", "") or "")
        if trigger and stage_id:
            consumers.setdefault(trigger, []).append(f"stage:{stage_id}")
    return consumers


def _failure_event_producers(config: ZfConfig) -> list[tuple[str, str]]:
    events: list[tuple[str, str]] = []
    for role in config.roles:
        role_ref = _role_ref(role)
        for event in role.publishes:
            event_str = str(event)
            if _event_tail(event_str) in FAILURE_SUFFIXES:
                events.append((event_str, role_ref))
    for stage in config.workflow.stages:
        aggregate = getattr(stage, "aggregate", None)
        event = str(getattr(aggregate, "failure_event", "") or "")
        if event and _event_tail(event) in FAILURE_SUFFIXES:
            events.append((event, f"stage:{getattr(stage, 'id', '')}"))
    return sorted(set(events))


def _role_ref(role: RoleConfig) -> str:
    return role.instance_id or role.name


def _event_tail(event: str) -> str:
    return event.rsplit(".", 1)[-1]

def _diag(
    *,
    severity: str,
    kind: str,
    message: str,
    source: str = "workflow_inspection",
    role: str = "",
    stage_id: str = "",
    event: str = "",
    field: str = "",
    detail: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "severity": severity,
        "kind": kind,
        "message": message,
        "source": source,
        "role": role,
        "stage_id": stage_id,
        "event": event,
        "field": field,
        "detail": detail or {},
    }


def _graph_message(item: dict[str, str]) -> str:
    kind = item.get("kind", "")
    event = item.get("event", "")
    stage_id = item.get("stage_id", "")
    field = item.get("field", "")
    target = item.get("target_role", "")
    if kind == "trigger_without_producer":
        return f"stage `{stage_id}` 的 trigger `{event}` 没有生产者"
    if kind == "missing_aggregate_success_event":
        return f"fanout stage `{stage_id}` 缺少 aggregate.success_event"
    if kind == "missing_aggregate_failure_event":
        return f"fanout stage `{stage_id}` 缺少 aggregate.failure_event"
    if kind == "event_without_consumer":
        return f"stage `{stage_id}` 的 `{field}` 事件 `{event}` 没有消费者"
    if kind == "invalid_rework_target":
        return f"rework event `{event}` 指向不存在的 role `{target}`"
    if kind == "missing_rework_route":
        return f"失败事件 `{event}` 缺少 rework route"
    return kind


def _status_from_diagnostics(diagnostics: list[dict[str, Any]]) -> str:
    status = "GO"
    for item in diagnostics:
        severity = str(item.get("severity", "INFO") or "INFO")
        if severity == "INFO":
            continue
        if _STATUS_ORDER.get(severity, 1) > _STATUS_ORDER[status]:
            status = severity
    return status if status in {"GO", "WARN", "STOP"} else "WARN"


def _diagnostic_counts(diagnostics: list[dict[str, Any]]) -> dict[str, int]:
    counts = {"STOP": 0, "WARN": 0, "INFO": 0}
    for item in diagnostics:
        severity = str(item.get("severity", "INFO") or "INFO")
        counts.setdefault(severity, 0)
        counts[severity] += 1
    return counts


def _dedupe_diagnostics(diagnostics: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: dict[tuple[str, str, str, str, str, str], dict[str, Any]] = {}
    for item in diagnostics:
        key = (
            str(item.get("severity", "")),
            str(item.get("kind", "")),
            str(item.get("role", "")),
            str(item.get("stage_id", "")),
            str(item.get("event", "")),
            str(item.get("field", "")),
        )
        deduped.setdefault(key, item)
    return list(deduped.values())
