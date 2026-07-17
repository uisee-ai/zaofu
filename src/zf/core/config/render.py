"""Effective config render/inspect helpers.

The active ``zf.yaml`` remains the only control-plane config.  This module only
materializes the loader output and records enough source metadata to make a
long-horizon run reproducible.
"""

from __future__ import annotations

from collections import Counter
import hashlib
import json
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from zf import __version__
from zf.core.config.schema import ZfConfig
from zf.core.state.atomic_io import atomic_write_text
from zf.core.workflow.inspection import build_workflow_inspection_report


_SECRET_KEY_PARTS = ("secret", "password", "private_key", "app_secret")
_STATUS_ORDER = {"GO": 0, "INFO": 1, "WARN": 2, "STOP": 3}
_PROJECT_SPECIFIC_TOKENS = ("cangjie", "hermes", "cj-min")
_ABSOLUTE_PATH_TOKENS = ("/home/", "/workspace/", "~/")
_PROJECT_SPECIFIC_KEYS = {
    "objective_ref",
    "objectiveref",
    "prompt_ref",
    "promptref",
    "source_root",
    "sourceroot",
    "target_root",
    "targetroot",
    "parity_scope",
    "parityscope",
}
_FLOW_POLICY_CONSUMERS = {
    "gap_loop": "RefactorFlow v3 generated module-parity loop",
    "verify_rescan": "RefactorFlow v3 generated verify.parity_scan.requested bridge",
    "completion_threshold": "module parity closeout / final judge gate",
    # ⑤ 续(2026-07-08)执法化:strict_refs 由 loader 派生
    # event_schema.mode=blocking + report_evidence_gate=fail_closed
    # (显式 verification.* 配置优先,逃生门)。
    "evidence_policy": (
        "loader derives event_schema.mode=blocking + "
        "report_evidence_gate=fail_closed for strict_refs "
        "(explicit verification.* wins)"
    ),
}
_FLOW_POLICY_METADATA_ONLY = {
    "goal_profile": "goal-loop closeout / delivery policy selector",
    "quality_floor": "quality gate profile / verification requirement matrix",
    "environment_policy": "verify env readiness / real provider probe",
    "post_verify_discovery": "flow-neutral post-verify discovery profile / gap scan",
    "projection_policy": "WebKanban / DeliveryTrace / Inbox projection contract",
}
_FLOW_POLICY_ENFORCEMENT_PLANS = {
    "gap_loop": {
        "enforcement_status": "wired",
        "target_gates": ["gap-plan bridge", "task_map.amended", "gap-scoped task_map.ready"],
        "rollout_phase": "kernel-wired",
        "risk": "low",
        "next_step": "Keep compatibility for gap_plan.ready / goal.gap_plan.ready / flow.gap_plan.ready.",
    },
    "verify_rescan": {
        "enforcement_status": "wired",
        "target_gates": ["verify.passed bridge", "post-verify discovery trigger"],
        "rollout_phase": "kernel-wired",
        "risk": "low",
        "next_step": "Continue keeping discovery profile explicit per Flow.",
    },
    "completion_threshold": {
        "enforcement_status": "wired",
        "target_gates": ["module parity closeout", "final judge readiness"],
        "rollout_phase": "kernel-wired",
        "risk": "medium",
        "next_step": "Extend the closeout threshold model to IssueFlow and PrdFlow before global strict mode.",
    },
    "goal_profile": {
        "enforcement_status": "metadata_only",
        "target_gates": ["delivery closeout", "goal closure judge"],
        "rollout_phase": "design/skill-first",
        "risk": "medium",
        "next_step": "Map goal_profile to an explicit delivery/closeout profile before treating it as a gate.",
    },
    "quality_floor": {
        "enforcement_status": "planned_consumer",
        "target_gates": ["verify readiness", "terminal evidence gate", "final judge gate"],
        "rollout_phase": "warn-before-strict",
        "risk": "high",
        "next_step": "Introduce flow-specific evidence matrices and enable strict mode on controller examples first.",
    },
    "evidence_policy": {
        # ⑤ 续(2026-07-08):kernel-wired——strict_refs 派生 blocking +
        # fail_closed(canonical-dag/v3 证据档 + U20 门),显式配置优先。
        "enforcement_status": "wired",
        "target_gates": [
            "terminal evidence gate", "event_schema blocking (canonical-dag/v3)",
            "report_evidence_gate fail_closed",
        ],
        "rollout_phase": "kernel-wired",
        "risk": "low",
        "next_step": (
            "Keep explicit verification.* as the escape hatch; extend "
            "derivation to other evidencePolicy values only with live proof."
        ),
    },
    "environment_policy": {
        "enforcement_status": "planned_consumer",
        "target_gates": ["start preflight", "verify env readiness", "real provider smoke"],
        "rollout_phase": "warn-before-strict",
        "risk": "high",
        "next_step": "Add cheap readiness checks first; keep real external provider calls opt-in per Flow.",
    },
    "post_verify_discovery": {
        "enforcement_status": "projection_wired",
        "target_gates": ["post-verify discovery projection", "goal gap task_map amend bridge"],
        "rollout_phase": "report-only-first",
        "risk": "medium",
        "next_step": "Add Flow-specific discovery skills before enabling automatic Issue/PRD gap task creation.",
    },
    "projection_policy": {
        "enforcement_status": "metadata_only",
        "target_gates": ["WebKanban", "DeliveryTrace", "Inbox"],
        "rollout_phase": "projection-only",
        "risk": "medium",
        "next_step": "Map projection_policy to concrete Web/Control Room view requirements.",
    },
}
_EXPECTED_FLOW_EVENT_SINKS = {
    "candidate.ready": "lane pipeline internal handoff / candidate integration",
    "test.passed": "RefactorFlow v3 verify bridge trigger",
    "verify.passed": "module parity bridge emits verify.parity_scan.requested",
    "flow.discovery.completed": "flow discovery closeout / gap-plan bridge",
    "flow.discovery.failed": "Run Manager / Autoresearch flow discovery recovery",
    "flow.gap_plan.ready": "goal gap-plan task_map amend bridge",
    "flow.goal.blocked": "Run Manager semantic recovery action",
    "flow.goal.closed": "delivery closeout / final goal closure projection",
    "verify.parity_scan.requested": "RefactorFlow v3 module parity scan trigger",
    "module.parity.scan.completed": "module parity closeout / gap-plan loop",
    "module.parity.scan.failed": "kernel candidate-rework sweep",
    "module.parity.closed": "RefactorFlow v3 final judge trigger",
}
_EXPECTED_FLOW_TRIGGER_SOURCES = {
    "flow.discovery.requested": "verify.passed post-verify discovery bridge",
    "flow.goal.closed": "flow discovery closeout bridge",
    "verify.parity_scan.requested": "verify.passed module parity bridge",
    "module.parity.closed": "module parity closeout bridge",
}
_EXPECTED_FLOW_EVENT_SUFFIX_SINKS = (
    (".module.parity.scan.completed", "module parity closeout / gap-plan loop"),
    (".module.parity.scan.failed", "kernel candidate-rework sweep"),
    (".lane.stage.failed", "kernel candidate-rework sweep"),
)
_SKILL_SCOPE_ALIASES = {
    "cli": ("cli", "contract", "parity"),
    "provider": ("provider",),
    "tools": ("tool", "contract-freeze", "parity-gate", "module-parity"),
    "gateway": ("gateway",),
    "webui": ("web", "dashboard", "tui"),
    "tui": ("tui", "web"),
    "memory": ("memory", "state-config", "source-inventory"),
    "skills": ("skill", "source-inventory", "parity-gate"),
    "pi-core": ("pi", "core", "parity"),
}
_DIAGNOSTIC_FIXITS = {
    "profile_boundary_violation": {
        "title": "Profile 分层边界不清",
        "why_it_matters": (
            "common/workflow profile 携带项目路径或项目事实时，复用到其它项目会"
            "产生隐藏漂移。"
        ),
        "fix_it": "把项目专属路径、prompt、source/target root 移到 profiles/project/<name>/。",
        "safe_auto_fix": False,
        "doc_ref": "docs/design/118-config-composition-workflow-controller-design.md",
    },
    "flow_policy_without_consumer": {
        "title": "Flow policy 尚未接入确定性消费者",
        "why_it_matters": "字段写在 YAML 里但不会影响 gate/runtime/Web，用户会误以为声明已生效。",
        "fix_it": "接入对应 gate/runtime/projection consumer，或删除该 policy 声明。",
        "safe_auto_fix": False,
        "doc_ref": "docs/design/119-refactor-goal-convergence-loop-yaml-design.md",
    },
    "flow_policy_consumer": {
        "title": "Flow policy 已接入消费者",
        "why_it_matters": "该 policy 不只是 metadata，render 已能解释由谁消费。",
        "fix_it": "无需处理。",
        "safe_auto_fix": False,
        "doc_ref": "docs/design/119-refactor-goal-convergence-loop-yaml-design.md",
    },
    "skill_coverage_gap": {
        "title": "Parity scope 缺少 skill 覆盖",
        "why_it_matters": "verify/rescan 可能漏掉该模块能力差距。",
        "fix_it": "给 scan/verify/module-parity/judge owner 增加覆盖该 scope 的 skill bundle。",
        "safe_auto_fix": False,
        "doc_ref": "docs/design/119-refactor-goal-convergence-loop-yaml-design.md",
    },
    "event_without_consumer": {
        "title": "事件没有消费者",
        "why_it_matters": "事件发出后没有下游 stage/runtime bridge 接住，workflow 可能停住。",
        "fix_it": "增加消费该事件的 stage、runtime bridge，或把它声明为 external/expected sink。",
        "safe_auto_fix": False,
        "doc_ref": "docs/design/118-config-composition-workflow-controller-design.md",
    },
    "expected_event_without_consumer": {
        "title": "事件由已知 runtime bridge 消费",
        "why_it_matters": "静态 graph 看不到该消费者，但 render 已确认这是已知闭环，不应误报。",
        "fix_it": "无需处理；若运行中仍卡住，再检查对应 runtime bridge 日志。",
        "safe_auto_fix": False,
        "doc_ref": "docs/design/119-refactor-goal-convergence-loop-yaml-design.md",
    },
    "trigger_without_producer": {
        "title": "Stage trigger 没有生产者",
        "why_it_matters": "该 stage 永远不会被触发，除非这是外部入口事件。",
        "fix_it": "补上上游 success event，或把该 trigger 加入 workflow.dag.external_triggers。",
        "safe_auto_fix": False,
        "doc_ref": "docs/design/118-config-composition-workflow-controller-design.md",
    },
    "missing_rework_route": {
        "title": "失败事件没有 rework 路由",
        "why_it_matters": "失败后无法确定该回到哪个角色/阶段，容易进入 blocked 或人工介入。",
        "fix_it": "在 stage on_failure/rework_routing 中声明目标，优先回同一 lane 的 impl owner。",
        "safe_auto_fix": False,
        "doc_ref": "docs/design/119-refactor-goal-convergence-loop-yaml-design.md",
    },
}


def build_config_inspection_report(
    config: ZfConfig,
    *,
    config_path: Path,
    project_root: Path,
    state_dir: Path,
) -> dict[str, Any]:
    workflow = build_workflow_inspection_report(
        config,
        project_root=project_root,
        state_dir=state_dir,
    )
    sources = list(getattr(config, "config_sources", []) or [])
    diagnostics = list(workflow.get("diagnostics", []) or [])
    project_tokens = _project_specific_tokens(config)
    profile_diagnostics = _profile_boundary_diagnostics(
        sources,
        project_tokens=project_tokens,
    )
    flow_policy_diagnostics = _flow_policy_consumer_diagnostics(config)
    skill_matrix, skill_diagnostics = _skill_coverage_matrix(config)
    diagnostics.extend(profile_diagnostics)
    diagnostics.extend(flow_policy_diagnostics)
    diagnostics.extend(skill_diagnostics)
    diagnostics = _classify_expected_event_sinks(config, diagnostics)
    diagnostics = _dedupe_diagnostics(diagnostics)
    diagnostics = _decorate_diagnostics(diagnostics)
    project_semantic_leakage = _project_semantic_leakage_report(
        sources,
        diagnostics,
        project_tokens=project_tokens,
    )
    status = _status_from_diagnostics(
        diagnostics,
        default=str(workflow.get("status", "GO") or "GO"),
    )
    report = {
        "schema_version": "config-inspection.v1",
        "project": dict(workflow.get("project") or {}),
        "source": {
            "path": str(config_path),
            "sha256": file_sha256(config_path),
            "profiles": sources,
        },
        "status": status,
        "summary": {
            "roles": len(config.roles),
            "stages": len(config.workflow.stages),
            "pipelines": len(config.workflow.pipelines),
            "event_schemas": len(config.workflow.dag.event_schemas),
            "profile_sources": len(sources),
            "diagnostics": _diagnostic_counts(diagnostics),
        },
        "diagnostics": diagnostics,
        "profile_diagnostics": profile_diagnostics,
        "flow_policy_diagnostics": flow_policy_diagnostics,
        "project_semantic_leakage": project_semantic_leakage,
        "coverage": {
            "skill_matrix": skill_matrix,
            "control_room_contract": _control_room_projection_contract(config),
        },
        "generated": {
            "roles": [_role_name(role) for role in config.roles],
            "stages": [
                {
                    "id": stage.id,
                    "trigger": stage.trigger,
                    "topology": stage.topology,
                    "roles": list(stage.roles),
                }
                for stage in config.workflow.stages
            ],
            "pipelines": [
                {
                    "id": getattr(pipeline, "pipeline_id", ""),
                    "trigger": getattr(pipeline, "trigger", ""),
                    "stage_transition": getattr(
                        pipeline,
                        "stage_transition",
                        "stage_barrier",
                    ),
                }
                for pipeline in config.workflow.pipelines
            ],
            "schema_sources": dict(config.workflow.pipelines_schema_sources),
            "flow_metadata": dict(
                getattr(config.workflow, "flow_metadata", {}) or {},
            ),
            "flow_metadata_by_kind": dict(
                getattr(config.workflow, "flow_metadata_by_kind", {}) or {},
            ),
        },
    }
    return report


def write_rendered_config(
    config: ZfConfig,
    *,
    config_path: Path,
    project_root: Path,
    state_dir: Path,
    output: Path,
    lock_path: Path,
    include_secrets: bool = False,
) -> dict[str, Any]:
    data = renderable_config_to_primitive(config)
    if not include_secrets:
        data = redact_config(data)
    output.parent.mkdir(parents=True, exist_ok=True)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(
        output,
        yaml.safe_dump(data, sort_keys=False, allow_unicode=True),
    )
    report = build_config_inspection_report(
        config,
        config_path=config_path,
        project_root=project_root,
        state_dir=state_dir,
    )
    lock = {
        "schema_version": "config-render-lock.v1",
        "rendered_at": datetime.now(timezone.utc).isoformat(),
        "zaofu_version": __version__,
        "input": report["source"],
        "output": {
            "path": str(output),
            "sha256": file_sha256(output),
            "redacted": not include_secrets,
        },
        "summary": report["summary"],
        "diagnostics": report["diagnostics"],
        "profile_diagnostics": report["profile_diagnostics"],
        "flow_policy_diagnostics": report["flow_policy_diagnostics"],
        "project_semantic_leakage": report["project_semantic_leakage"],
        "coverage": report["coverage"],
        "generated": report["generated"],
    }
    atomic_write_text(
        lock_path,
        json.dumps(lock, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )
    return lock


def renderable_config_to_primitive(config: ZfConfig) -> dict[str, Any]:
    """Return a canonical YAML shape that the config loader can read again."""

    data = config_to_primitive(config)
    if not isinstance(data, dict):
        return {}
    # safety is stored flat (SafetyConfig.tool_closure_enabled) but the loader
    # reads it nested (safety.tool_closure.enabled). Re-nest so the rendered YAML
    # round-trips through the loader's fail-closed key check (P1-3) instead of
    # emitting a flat key the loader now rejects. Same class of non-round-trip
    # fixup as the provenance-field pops below.
    safety = data.get("safety")
    if isinstance(safety, dict) and "tool_closure_enabled" in safety:
        enabled = safety.pop("tool_closure_enabled")
        safety["tool_closure"] = {"enabled": bool(enabled)}
    roles = data.get("roles")
    if isinstance(roles, list):
        for role in roles:
            if not isinstance(role, dict):
                continue
            if role.get("backend") and not role.get("backends"):
                role.pop("backends", None)

    workflow = data.get("workflow")
    if not isinstance(workflow, dict):
        return data

    stages = workflow.get("stages")
    if isinstance(stages, list):
        for stage in stages:
            if not isinstance(stage, dict):
                continue
            _normalize_rendered_stage_fanout(stage)
            for field in ("on_reject", "on_fail"):
                backedge = stage.get(field)
                if isinstance(backedge, dict) and not str(
                    backedge.get("event") or ""
                ).strip():
                    stage.pop(field, None)

    plan_approval_enabled = workflow.pop("plan_approval_enabled", None)
    if plan_approval_enabled is not None:
        workflow["plan_approval"] = {"enabled": bool(plan_approval_enabled)}

    flow_metadata = workflow.pop("flow_metadata", None)
    if isinstance(flow_metadata, dict) and flow_metadata:
        rendered_flow_metadata = dict(flow_metadata)
        if workflow.get("pipelines") and workflow.get("stages"):
            rendered_flow_metadata["rendered_pipeline_stages"] = True
        workflow["_flow_metadata"] = rendered_flow_metadata

    flow_metadata_by_kind = workflow.pop("flow_metadata_by_kind", None)
    if isinstance(flow_metadata_by_kind, dict) and flow_metadata_by_kind:
        workflow["_flow_metadata_by_kind"] = flow_metadata_by_kind

    pipelines = workflow.get("pipelines")
    if isinstance(pipelines, list):
        workflow["pipelines"] = [
            _renderable_lane_pipeline(item)
            for item in pipelines
            if isinstance(item, dict)
        ]

    # These are inspect/render-lock provenance fields derived by the loader.
    # Keeping them in rendered YAML makes the output fail the loader's
    # unknown-key guard and turns config render into a non-round-trippable
    # artifact. They remain available in the render lock.
    workflow.pop("pipelines_role_meta", None)
    workflow.pop("pipelines_schema_sources", None)
    return data


def _normalize_rendered_stage_fanout(stage: dict[str, Any]) -> None:
    """Render stage fields in the shape the loader actually consumes.

    ``WorkflowStageConfig`` stores ``assignment`` / ``children`` as top-level
    dataclass fields, while the source YAML contract puts them under
    ``fanout``.  Config render output is often copied to ``zf.yaml`` and loaded
    again; leaving these fields top-level silently drops affinity scheduling on
    reload.
    """

    assignment = stage.pop("assignment", None)
    children = stage.pop("children", None)
    fanout = stage.get("fanout")
    if fanout is not None and not isinstance(fanout, dict):
        fanout = {}
        stage["fanout"] = fanout

    needs_fanout = False
    if isinstance(assignment, dict) and _rendered_assignment_has_contract(
        assignment,
    ):
        fanout = fanout if isinstance(fanout, dict) else {}
        fanout.setdefault("assignment", assignment)
        needs_fanout = True
    if isinstance(children, list) and children:
        fanout = fanout if isinstance(fanout, dict) else {}
        fanout.setdefault("children", children)
        needs_fanout = True

    if needs_fanout:
        stage["fanout"] = fanout
    elif isinstance(fanout, dict) and not fanout:
        stage.pop("fanout", None)


def _rendered_assignment_has_contract(assignment: dict[str, Any]) -> bool:
    strategy = str(assignment.get("strategy") or "").strip()
    lane_profile = str(assignment.get("lane_profile") or "").strip()
    stage_slot = str(assignment.get("stage_slot") or "").strip()
    role_pool = assignment.get("role_pool") or []
    if isinstance(role_pool, list):
        has_role_pool = any(str(item).strip() for item in role_pool)
    else:
        has_role_pool = bool(role_pool)
    return (
        bool(strategy and strategy != "static_index")
        or has_role_pool
        or bool(lane_profile)
        or bool(stage_slot)
    )


def _renderable_lane_pipeline(item: dict[str, Any]) -> dict[str, Any]:
    """Map parsed LanePipelineSpec dataclass fields back to source DSL keys."""

    pipeline: dict[str, Any] = {
        "id": str(item.get("pipeline_id") or item.get("id") or ""),
        "kind": "lane_pipeline",
        "trigger": str(item.get("trigger") or ""),
        "affinity_key": str(item.get("affinity_key") or ""),
        "lane_count": int(item.get("lane_count") or 0),
        "overflow": str(item.get("overflow") or "first_released_lane"),
        "max_rework_attempts": int(item.get("max_rework_attempts") or 2),
        "trace_budget": int(item.get("trace_budget") or 0),
        "require_artifact_digests": bool(
            item.get("require_artifact_digests", False)
        ),
        "stages": [],
    }
    task_map_ref = str(item.get("task_map_ref") or "")
    if task_map_ref:
        pipeline["task_source"] = {"task_map_ref": task_map_ref}

    stages = item.get("stages")
    if isinstance(stages, list):
        for stage in stages:
            if not isinstance(stage, dict):
                continue
            rendered_stage: dict[str, Any] = {
                "id": str(stage.get("stage_id") or stage.get("id") or ""),
                "role_pattern": str(stage.get("role_pattern") or ""),
            }
            terminal = {
                "success": str(stage.get("success_event") or ""),
                "failure": str(stage.get("failure_event") or ""),
            }
            if terminal["success"] or terminal["failure"]:
                rendered_stage["terminal"] = terminal
            on_failure = {
                "rework_to": str(stage.get("rework_to") or ""),
                "feedback_artifact": str(stage.get("feedback_artifact") or ""),
            }
            if on_failure["rework_to"] or on_failure["feedback_artifact"]:
                rendered_stage["on_failure"] = on_failure
            deadline = int(stage.get("deadline_seconds") or 0)
            if deadline:
                rendered_stage["deadline_seconds"] = deadline
            pipeline["stages"].append(rendered_stage)

    final = {
        "when": str(item.get("final_when") or ""),
        "role": str(item.get("final_role") or ""),
        "success": str(item.get("final_success") or ""),
        "failure": str(item.get("final_failure") or ""),
    }
    if any(final.values()):
        pipeline["final"] = final

    barriers = {
        "stage_transition": str(item.get("stage_transition") or ""),
        "final": str(item.get("final_barrier") or ""),
    }
    if any(barriers.values()):
        pipeline["barriers"] = barriers

    template = item.get("lane_role_template")
    if isinstance(template, dict) and template:
        pipeline["lane_role_template"] = template

    for key in ("schema_profile", "schema_overrides", "instruction_refs"):
        value = item.get(key)
        if value:
            pipeline[key] = value

    if item.get("assembly_declared"):
        if item.get("assembly_none"):
            pipeline["assembly"] = "none"
        elif item.get("assembly_task"):
            pipeline["assembly"] = {"task": str(item.get("assembly_task") or "")}

    return pipeline


def config_to_primitive(value: Any) -> Any:
    if is_dataclass(value):
        return {
            key: config_to_primitive(item)
            for key, item in asdict(value).items()
        }
    if isinstance(value, dict):
        return {str(k): config_to_primitive(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [config_to_primitive(item) for item in value]
    return value


def redact_config(value: Any, *, parent_key: str = "") -> Any:
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, item in value.items():
            key_s = str(key)
            if _is_secret_key(key_s):
                out[key_s] = "<redacted>"
            else:
                out[key_s] = redact_config(item, parent_key=key_s)
        return out
    if isinstance(value, list):
        return [redact_config(item, parent_key=parent_key) for item in value]
    return value


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_secret_key(key: str) -> bool:
    normalized = key.lower()
    if any(part in normalized for part in _SECRET_KEY_PARTS):
        return True
    if normalized in {
        "token",
        "access_token",
        "refresh_token",
        "api_token",
        "bearer_token",
    }:
        return True
    return normalized.endswith("_token") or normalized.endswith("_token_env")


def _role_name(role: Any) -> str:
    return str(getattr(role, "instance_id", "") or getattr(role, "name", ""))


def _project_specific_tokens(config: ZfConfig | None) -> tuple[str, ...]:
    """Builtin baseline plus tokens derived from the active project name.

    The baseline alone only protects historical refactor targets; deriving
    from ``project.name`` makes the boundary lint flag the *current* project's
    semantics leaking into common/workflow profiles too. Short names (<3
    chars) are skipped — they over-match generic values.
    """
    tokens = list(_PROJECT_SPECIFIC_TOKENS)
    name = str(getattr(getattr(config, "project", None), "name", "") or "").strip().lower()
    for variant in (name, name.replace("_", "-"), name.replace("-", "_")):
        if len(variant) >= 3 and variant not in tokens:
            tokens.append(variant)
    return tuple(tokens)


def _profile_boundary_diagnostics(
    sources: list[dict[str, Any]],
    *,
    project_tokens: tuple[str, ...] = _PROJECT_SPECIFIC_TOKENS,
) -> list[dict[str, Any]]:
    diagnostics: list[dict[str, Any]] = []
    for source in sources:
        if str(source.get("kind") or "") != "ProfileSource":
            continue
        path = Path(str(source.get("path") or ""))
        layer = _profile_layer(path)
        if not layer:
            continue
        try:
            documents = list(yaml.safe_load_all(path.read_text(encoding="utf-8")))
        except Exception as exc:  # pragma: no cover - defensive inspect only.
            diagnostics.append(_diag(
                severity="WARN",
                kind="profile_source_unreadable",
                message=f"profile source `{path}` 无法读取或解析: {exc}",
                source="config_profile_boundary",
                field=str(path),
            ))
            continue
        for doc in documents:
            if not isinstance(doc, dict):
                continue
            spec = doc.get("spec") or {}
            if not isinstance(spec, dict):
                continue
            keys = {item.lower().replace(".", "_") for item in _flatten_keys(spec)}
            values = [str(item).lower() for item in _iter_scalar_values(spec)]
            if layer == "common" or layer.startswith("workflow/"):
                offenders = _boundary_offenders(
                    keys,
                    values,
                    strict_project=True,
                    project_tokens=project_tokens,
                )
            else:
                offenders = []
            for offender in offenders:
                diagnostics.append(_diag(
                    severity="WARN",
                    kind="profile_boundary_violation",
                    message=(
                        f"profile `{path}` 位于 `{layer}` 层,但包含 `{offender}`；"
                        "common/workflow profile 不应携带项目路径、prompt、"
                        "source/target root 或 Cangjie/Hermes 专属事实"
                    ),
                    source="config_profile_boundary",
                    field=str(path),
                    detail={"layer": layer, "offender": offender},
                ))
    return diagnostics


def _project_semantic_leakage_report(
    sources: list[dict[str, Any]],
    diagnostics: list[dict[str, Any]],
    *,
    project_tokens: tuple[str, ...] = _PROJECT_SPECIFIC_TOKENS,
) -> dict[str, Any]:
    """Structured view of project-semantic ownership for config inspect.

    Diagnostics stay as the machine-readable WARN/INFO stream. This report is a
    reviewer-facing grouping so product operators can distinguish real common
    profile leakage from project adapter ownership.
    """

    entries: list[dict[str, Any]] = []
    for item in diagnostics:
        if str(item.get("kind") or "") != "profile_boundary_violation":
            continue
        detail = dict(item.get("detail") or {})
        entries.append({
            "category": "runtime_contract_violation",
            "path": str(item.get("field") or ""),
            "layer": str(detail.get("layer") or ""),
            "token": str(detail.get("offender") or ""),
            "message": str(item.get("message") or ""),
        })
    for entry in _project_profile_semantics(sources, project_tokens=project_tokens):
        if entry not in entries:
            entries.append(entry)
    counts = Counter(str(entry.get("category") or "") for entry in entries)
    return {
        "schema_version": "project-semantic-leakage.v1",
        "entry_count": len(entries),
        "counts": dict(sorted(counts.items())),
        "entries": entries,
    }


def _project_profile_semantics(
    sources: list[dict[str, Any]],
    *,
    project_tokens: tuple[str, ...] = _PROJECT_SPECIFIC_TOKENS,
) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for source in sources:
        if str(source.get("kind") or "") != "ProfileSource":
            continue
        path = Path(str(source.get("path") or ""))
        layer = _profile_layer(path)
        if not layer.startswith("project/"):
            continue
        try:
            documents = list(yaml.safe_load_all(path.read_text(encoding="utf-8")))
        except Exception:
            continue
        for doc in documents:
            if not isinstance(doc, dict):
                continue
            spec = doc.get("spec") or {}
            if not isinstance(spec, dict):
                continue
            keys = {item.lower().replace(".", "_") for item in _flatten_keys(spec)}
            values = [str(item).lower() for item in _iter_scalar_values(spec)]
            offenders = _boundary_offenders(
                keys,
                values,
                strict_project=True,
                project_tokens=project_tokens,
            )
            for offender in offenders:
                entries.append({
                    "category": "adapter_skill_owned",
                    "path": str(path),
                    "layer": layer,
                    "token": offender,
                    "message": (
                        "project profile may own project-specific semantics; "
                        "keep them out of common/workflow profiles"
                    ),
                })
    return entries


def _profile_layer(path: Path) -> str:
    parts = [part.lower() for part in path.parts]
    if "profiles" not in parts:
        return ""
    idx = parts.index("profiles")
    rest = parts[idx + 1:]
    if not rest:
        return ""
    if rest[0] == "common":
        return "common"
    if rest[0] == "workflow" and len(rest) >= 2:
        return f"workflow/{rest[1]}"
    if rest[0] == "project" and len(rest) >= 2:
        return f"project/{rest[1]}"
    return rest[0]


def _boundary_offenders(
    keys: set[str],
    values: list[str],
    *,
    strict_project: bool,
    project_tokens: tuple[str, ...] = _PROJECT_SPECIFIC_TOKENS,
) -> list[str]:
    offenders: list[str] = []
    for key in sorted(keys):
        compact = key.replace("_", "")
        if key in _PROJECT_SPECIFIC_KEYS or compact in _PROJECT_SPECIFIC_KEYS:
            offenders.append(key)
    for value in values:
        if any(token in value for token in _ABSOLUTE_PATH_TOKENS):
            offenders.append(value)
            continue
        if strict_project and any(token in value for token in project_tokens):
            offenders.append(value)
    return sorted(set(offenders))


def _flatten_keys(value: Any, *, prefix: str = "") -> list[str]:
    if isinstance(value, dict):
        out: list[str] = []
        for key, item in value.items():
            child = f"{prefix}.{key}" if prefix else str(key)
            out.append(child)
            out.extend(_flatten_keys(item, prefix=child))
        return out
    if isinstance(value, list):
        out: list[str] = []
        for item in value:
            out.extend(_flatten_keys(item, prefix=prefix))
        return out
    return []


def _iter_scalar_values(value: Any) -> list[Any]:
    if isinstance(value, dict):
        out: list[Any] = []
        for item in value.values():
            out.extend(_iter_scalar_values(item))
        return out
    if isinstance(value, list):
        out: list[Any] = []
        for item in value:
            out.extend(_iter_scalar_values(item))
        return out
    if isinstance(value, (str, int, float, bool)):
        return [value]
    return []


def _flow_policy_consumer_diagnostics(config: ZfConfig) -> list[dict[str, Any]]:
    metadata = dict(getattr(config.workflow, "flow_metadata", {}) or {})
    diagnostics: list[dict[str, Any]] = []
    for key, consumer in sorted(_FLOW_POLICY_CONSUMERS.items()):
        if metadata.get(key) in (None, "", [], {}):
            continue
        plan = dict(_FLOW_POLICY_ENFORCEMENT_PLANS.get(key) or {})
        diagnostics.append(_diag(
            severity="INFO",
            kind="flow_policy_consumer",
            message=f"Flow policy `{key}` has consumer: {consumer}",
            source="config_flow_policy",
            field=key,
            detail={
                "value": metadata.get(key),
                "consumer": consumer,
                "ownership": "kernel",
                "consumer_status": "wired",
                **plan,
                "decision_boundary": (
                    "deterministic code may own policies that affect "
                    "state transitions, evidence gates, replay/resume, or "
                    "external side effects"
                ),
            },
        ))
    for key, suggested_consumer in sorted(_FLOW_POLICY_METADATA_ONLY.items()):
        value = metadata.get(key)
        if value in (None, "", [], {}):
            continue
        plan = dict(_FLOW_POLICY_ENFORCEMENT_PLANS.get(key) or {})
        next_step = str(plan.get("next_step") or suggested_consumer)
        diagnostics.append(_diag(
            severity="WARN",
            kind="flow_policy_without_consumer",
            message=(
                f"Flow policy `{key}` 当前仅作为 metadata/briefing "
                "保留,尚未证明被 deterministic gate/runtime/Web consumer 执行"
            ),
            source="config_flow_policy",
            field=key,
            detail={
                "value": value,
                "suggested_consumer": suggested_consumer,
                "ownership": "skill/prompt/agent-artifact",
                "consumer_status": "metadata_only",
                **plan,
                "decision_boundary": (
                    "semantic or project-specific policy should stay in "
                    "skill/prompt/agent artifacts until it becomes a stable "
                    "cross-flow invariant"
                ),
            },
            fix_it=(
                f"下一步接入 `{key}` 的 deterministic consumer: {next_step}"
            ),
        ))
    return diagnostics


def _classify_expected_event_sinks(
    config: ZfConfig,
    diagnostics: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Downgrade known runtime bridge/sweep sinks from generic graph WARN.

    Workflow graph inspection is intentionally static. RefactorFlow v3 has a
    few deterministic runtime bridges that are not modeled as explicit reader
    stages, so a raw ``event_without_consumer`` can be a false warning. Keep
    unknown events as WARN, but classify documented kernel/bridge sinks as INFO
    with an explicit expected consumer.
    """
    kernel_swept = {
        str(item.get("event") or "")
        for item in diagnostics
        if str(item.get("kind") or "") == "kernel_swept_failure_event"
    }
    try:
        from zf.core.workflow.topology import derive_kernel_swept_events

        kernel_swept.update(derive_kernel_swept_events(
            getattr(config.workflow, "stages", []) or [],
            getattr(config.workflow, "pipelines", []) or [],
        ))
    except Exception:
        pass
    flow_metadata = dict(getattr(config.workflow, "flow_metadata", {}) or {})
    has_flow_discovery_bridge = bool(flow_metadata.get("post_verify_discovery")) or any(
        str(getattr(stage, "trigger", "") or "") == "flow.discovery.requested"
        for stage in getattr(config.workflow, "stages", []) or []
    )
    has_refactor_goal_loop = (
        bool(flow_metadata.get("gap_loop") or flow_metadata.get("verify_rescan"))
        or _has_module_parity_bridge_shape(config)
    )
    out: list[dict[str, Any]] = []
    for item in diagnostics:
        kind = str(item.get("kind") or "")
        if kind == "trigger_without_producer":
            event = str(item.get("event") or "")
            expected_source = ""
            if has_flow_discovery_bridge or has_refactor_goal_loop:
                expected_source = _EXPECTED_FLOW_TRIGGER_SOURCES.get(event, "")
            if not expected_source:
                out.append(item)
                continue
            detail = dict(item.get("detail") or {})
            detail["expected_source"] = expected_source
            detail["original_kind"] = item.get("kind", "")
            detail["original_source"] = item.get("source", "")
            classified = dict(item)
            classified.update({
                "severity": "INFO",
                "kind": "expected_trigger_without_producer",
                "source": "config_expected_source",
                "message": (
                    f"trigger `{event}` 没有静态 graph producer，但由 "
                    f"{expected_source} 生产"
                ),
                "detail": detail,
            })
            out.append(classified)
            continue
        if kind != "event_without_consumer":
            out.append(item)
            continue
        event = str(item.get("event") or "")
        expected_consumer = ""
        if event in kernel_swept:
            expected_consumer = "kernel candidate-rework sweep"
        elif has_flow_discovery_bridge or has_refactor_goal_loop:
            expected_consumer = _expected_flow_sink_consumer(event)
        if not expected_consumer:
            out.append(item)
            continue
        detail = dict(item.get("detail") or {})
        detail["expected_consumer"] = expected_consumer
        detail["original_kind"] = item.get("kind", "")
        detail["original_source"] = item.get("source", "")
        classified = dict(item)
        classified.update({
            "severity": "INFO",
            "kind": "expected_event_without_consumer",
            "source": "config_expected_sink",
            "message": (
                f"事件 `{event}` 没有显式 graph consumer，但由 "
                f"{expected_consumer} 消费/闭合"
            ),
            "detail": detail,
        })
        out.append(classified)
    return out


def _expected_flow_sink_consumer(event: str) -> str:
    direct = _EXPECTED_FLOW_EVENT_SINKS.get(event)
    if direct:
        return direct
    for suffix, consumer in _EXPECTED_FLOW_EVENT_SUFFIX_SINKS:
        if event.endswith(suffix):
            return consumer
    return ""


def _has_module_parity_bridge_shape(config: ZfConfig) -> bool:
    """Detect hand-written refactor YAMLs that wire the parity bridge directly."""

    triggers = {
        str(getattr(stage, "trigger", "") or "")
        for stage in getattr(config.workflow, "stages", []) or []
    }
    return "verify.parity_scan.requested" in triggers or "module.parity.closed" in triggers


def _skill_coverage_matrix(config: ZfConfig) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    metadata = dict(getattr(config.workflow, "flow_metadata", {}) or {})
    scope = [str(item).lower() for item in list(metadata.get("parity_scope") or [])]
    matrix: dict[str, Any] = {}
    diagnostics: list[dict[str, Any]] = []
    if not scope:
        return matrix, diagnostics
    role_rows = []
    for role in config.roles:
        role_rows.append({
            "role": _role_name(role),
            "skills": [str(skill).lower() for skill in list(getattr(role, "skills", []) or [])],
        })
    for item in scope:
        aliases = _SKILL_SCOPE_ALIASES.get(item, (item,))
        owners = []
        for row in role_rows:
            matched = sorted({
                skill
                for skill in row["skills"]
                if any(alias in skill for alias in aliases)
            })
            if matched:
                owners.append({"role": row["role"], "skills": matched})
        matrix[item] = {
            "aliases": list(aliases),
            "owners": owners,
            "covered": bool(owners),
        }
        if not owners:
            diagnostics.append(_diag(
                severity="WARN",
                kind="skill_coverage_gap",
                message=(
                    f"parity scope `{item}` 没有找到对应 role skill owner；"
                    "verify/rescan 可能无法稳定发现该模块缺口"
                ),
                source="config_skill_coverage",
                field=item,
            ))
    return matrix, diagnostics


def _control_room_projection_contract(config: ZfConfig) -> dict[str, Any]:
    metadata = dict(getattr(config.workflow, "flow_metadata", {}) or {})
    policy = str(metadata.get("projection_policy") or "").strip()
    enabled = policy == "control_room"
    fields = [
        "current_stage",
        "blocked_reason",
        "next_owner",
        "pending_action",
        "latest_evidence",
        "gap_count",
    ]
    return {
        "schema_version": "control-room-projection-contract.v1",
        "enabled": enabled,
        "policy": policy,
        "required_fields": fields if enabled else [],
        "event_sources": [
            "task_map.ready",
            "fanout.started",
            "verify.passed",
            "flow.discovery.requested",
            "flow.discovery.completed",
            "flow.gap_plan.ready",
            "flow.goal.blocked",
            "flow.goal.closed",
            "run.manager.action.applied",
        ] if enabled else [],
    }


def _diag(
    *,
    severity: str,
    kind: str,
    message: str,
    source: str,
    field: str = "",
    detail: dict[str, Any] | None = None,
    **extra: Any,
) -> dict[str, Any]:
    out = {
        "severity": severity,
        "kind": kind,
        "message": message,
        "source": source,
        "role": "",
        "stage_id": "",
        "event": "",
        "field": field,
        "detail": detail or {},
    }
    out.update(extra)
    return out


def _decorate_diagnostics(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [_decorate_diagnostic(item) for item in items]


def _decorate_diagnostic(item: dict[str, Any]) -> dict[str, Any]:
    kind = str(item.get("kind") or "")
    fixit = _DIAGNOSTIC_FIXITS.get(kind)
    out = dict(item)
    if fixit:
        for key, value in fixit.items():
            out.setdefault(key, value)
    else:
        out.setdefault("title", kind.replace("_", " ") or "Config diagnostic")
        out.setdefault(
            "why_it_matters",
            "该诊断可能影响 workflow 可启动性、可恢复性或可解释性。",
        )
        out.setdefault("fix_it", "查看 message/detail 后按字段修正配置。")
        out.setdefault("safe_auto_fix", False)
        out.setdefault("doc_ref", "docs/design/118-config-composition-workflow-controller-design.md")
    severity = str(out.get("severity") or "INFO").upper()
    if severity == "STOP":
        out.setdefault("severity_class", "block_start")
    elif severity == "WARN":
        out.setdefault("severity_class", "warn")
    else:
        out.setdefault("severity_class", "info")
    return out


def _status_from_diagnostics(diagnostics: list[dict[str, Any]], *, default: str) -> str:
    if not diagnostics:
        status = default if default in _STATUS_ORDER else "GO"
        return "STOP" if status == "STOP" else "WARN" if status == "WARN" else "GO"
    status = "GO"
    for item in diagnostics:
        severity = str(item.get("severity", "INFO") or "INFO")
        if _STATUS_ORDER.get(severity, 1) > _STATUS_ORDER.get(status, 0):
            status = severity
    return "STOP" if status == "STOP" else "WARN" if status == "WARN" else "GO"


def _diagnostic_counts(diagnostics: list[dict[str, Any]]) -> dict[str, int]:
    counts = {"STOP": 0, "WARN": 0, "INFO": 0}
    for item in diagnostics:
        severity = str(item.get("severity", "INFO") or "INFO")
        counts.setdefault(severity, 0)
        counts[severity] += 1
    return counts


def _dedupe_diagnostics(diagnostics: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: dict[tuple[str, str, str, str, str], dict[str, Any]] = {}
    for item in diagnostics:
        key = (
            str(item.get("severity", "")),
            str(item.get("kind", "")),
            str(item.get("source", "")),
            str(item.get("field", "")),
            str(item.get("message", "")),
        )
        out.setdefault(key, item)
    return list(out.values())
