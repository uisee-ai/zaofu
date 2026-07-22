"""flowProfile — 工作流形状随发行库(doc 90 §9.12,V2)。

schemaProfile(A2)的同手法应用于**拓扑本身**:scan/plan/lane 链/judge
的完整形状以 `/vN` 不可变形状发行,项目 yaml 只剩参数。展开产物全部是
既有原语(canonical stages + lane_pipeline + dag.schema_profile),进
单一 ZfConfig——无第二控制面、无第二 scheduler。

形状基线:hermes cj-min R21-R24 实跑 24 轮的 scan/plan/lane 形状
(/v1 冻结;形状再变发 /v2)。

三源守门(V2):用户手写 stages 最高——profile 展开的 stage 若与手写
同 trigger,跳过该 stage 并 stderr WARN(与 G3 lane 物化同一规则)。
"""

from __future__ import annotations

import sys
from typing import Any

from zf.core.events.module_parity import (
    MODULE_PARITY_SCAN_COMPLETED,
    MODULE_PARITY_SCAN_FAILED,
    MODULE_PARITY_SCAN_REQUESTED,
)


GOAL_CLOSURE_SYNTHESIZED = "goal.closure.synthesized"
GOAL_CLOSURE_SYNTHESIS_FAILED = "goal.closure.synthesis.failed"


class WorkflowProfileError(ValueError):
    """profile 引用/参数错误——envelope/loader 包装为 ConfigError。"""


_KNOWN_PARAM_KEYS = frozenset({
    "flowProfile", "flow_profile",
    "lanes", "laneCount", "lane_count",
    "assembly", "budgets",
    "laneRoleTemplate", "lane_role_template",
    "entryTrigger", "entry_trigger",
    "scan", "judgeRole", "judge_role",
    "schemaProfile", "schema_profile",
})
_KNOWN_PARAM_KEYS_V3 = _KNOWN_PARAM_KEYS | frozenset({
    "objectiveRef", "objective_ref",
    "targetRef", "target_ref",
    "sourceRoot", "source_root",
    "targetRoot", "target_root",
    "parityScope", "parity_scope",
    "gapLoop", "gap_loop",
    "verifyRescan", "verify_rescan",
    "completionThreshold", "completion_threshold",
    "postVerifyDiscovery", "post_verify_discovery",
    "goalProfile", "goal_profile",
    "qualityFloor", "quality_floor",
    "evidencePolicy", "evidence_policy",
    "environmentPolicy", "environment_policy",
    "projectionPolicy", "projection_policy",
    "deliveryPolicy", "delivery_policy",
    "moduleParityRole", "module_parity_role",
    "verifyBridgeRole", "verify_bridge_role",
    "roleDefaults", "role_defaults",
    "roleSkillBundles", "role_skill_bundles",
})
_COMMON_PRODUCT_FLOW_KEYS = frozenset({
    "flowProfile", "flow_profile",
    "lanes", "laneCount", "lane_count",
    "backend",
    "entryTrigger", "entry_trigger",
    "taskMapRef", "task_map_ref",
    "roleDefaults", "role_defaults",
    "roleSkillBundles", "role_skill_bundles",
    "qualityFloor", "quality_floor",
    "evidencePolicy", "evidence_policy",
    "environmentPolicy", "environment_policy",
    "projectionPolicy", "projection_policy",
    "deliveryPolicy", "delivery_policy",
    "postVerifyDiscovery", "post_verify_discovery",
})
_KNOWN_ISSUE_FLOW_KEYS = _COMMON_PRODUCT_FLOW_KEYS | frozenset({
    "topology",  # fanout(default) | light(single lane goal loop)
    "issueRef", "issue_ref",
    "targetRef", "target_ref",
    "triageRole", "triage_role",
    "fixRolePattern", "fix_role_pattern",
    "targetRoot", "target_root",
    "verifyRolePattern", "verify_role_pattern",
    "discoveryRole", "discovery_role",
    "judgeRole", "judge_role",
})
_KNOWN_PRD_FLOW_KEYS = _COMMON_PRODUCT_FLOW_KEYS | frozenset({
    "topology",  # 批D: fanout(默认)| light(单 lane goal 环)
    "prdRef", "prd_ref",
    "targetRef", "target_ref",
    "scanRoles", "scan_roles",
    "planRole", "plan_role",
    "implRolePattern", "impl_role_pattern",
    "verifyRolePattern", "verify_role_pattern",
    "discoveryRole", "discovery_role",
    "judgeRole", "judge_role",
    "targetRoot", "target_root",
    "stack",
})

# refactor-flow/v1 的 scan child 缺省三件套(id 即专长;指令正文住
# skills/PRD,经 instruction_ref 在 briefing 期物化 —— W1)。
_SCAN_CHILDREN_V1 = ("scan-contract", "scan-runtime", "scan-verification")

_PRD_SCAN_INSTRUCTIONS = [
    "This is the initial PRD scan stage, not implementation verification.",
    "Missing product code is expected before impl; record it as required work, risk, or gap input, not as a blocking scan failure.",
    "Emit success when you can read the PRD/scope and produce a useful product/technical scan report for planning.",
    "Emit failure only when the PRD/scope cannot be read or the scan report cannot be produced.",
]

_TASK_MAP_CONTRACT_INSTRUCTIONS = [
    "Task-map hard contract: write valid JSON with top-level `schema_version` exactly `task-map.v1`.",
    "Task-map hard contract: when the workflow has a target root, include top-level `target_root` and keep all paths repo-relative.",
    "Task-map hard contract: include `shared_conventions.test_path_prefix` and package/scaffold conventions such as `package_root` and `packaging_file` when more than one task depends on them.",
    "Task-map hard contract: top-level `tasks` must be a non-empty array; each task must have a stable `task_id`, `title`, scoped `allowed_paths`, executable `verification`, `acceptance_criteria`, and `blocked_by` (empty array when none).",
    "Task-map hard contract: when `allowed_paths` is non-empty, include `allowed_paths_reason` or `scope_reason` explaining the ownership boundary.",
    "Task-map hard contract: dependencies in `blocked_by` must reference existing task ids and must not point to a later wave.",
    "Task-map hard contract: `verification` must be an executable shell command only, not prose; put explanatory text in acceptance criteria or evidence fields.",
    "Task-map hard contract: verification may use `cd <target_root>`, but referenced files must still be represented by repo-relative `allowed_paths` such as `app/tests/...`, not bare `tests/...`.",
    "Task-map hard contract: scaffold owners must list package metadata files they create, including `package.json`, `pyproject.toml`, `setup.py`, `setup.cfg`, `tsconfig.json`, or lockfiles.",
]

_PRD_PLAN_INSTRUCTIONS = [
    "Convert the completed PRD scan into a machine-readable task_map and a human-readable plan.",
    "The task_map must be JSON and must contain implementable tasks with stable ids, owned paths, acceptance criteria, and verification.",
    "Do not treat unimplemented product requirements as plan failure; they belong in the task_map.",
    *_TASK_MAP_CONTRACT_INSTRUCTIONS,
]

_ISSUE_TRIAGE_INSTRUCTIONS = [
    "This is issue triage and task-map synthesis, not fix verification.",
    "The reported bug or missing behavior is expected before fix; turn it into scoped repair tasks and regression checks.",
    "Emit success when you can understand the issue and produce a machine-readable task_map.",
    "Emit failure only when the issue/scope cannot be read or no actionable task_map can be produced.",
    *_TASK_MAP_CONTRACT_INSTRUCTIONS,
]

_POST_VERIFY_DISCOVERY_INSTRUCTIONS = [
    "This stage runs after implementation verification and should inspect the delivered candidate for residual gaps.",
    "Use concrete evidence from files, tests, and prior artifacts; emit failure for blocking product/regression/parity gaps that still need rework.",
    "If no blocking gaps remain, emit success with explicit zero-gap closure evidence: include `evidence_refs` and at least one of `test_refs`, `e2e_refs`, `demo_refs`, `regression_refs`, or `parity_refs` as applicable.",
    "If gaps remain, emit bounded `gap_tasks` with source refs and verification commands instead of a prose-only finding.",
]

_FINAL_JUDGE_INSTRUCTIONS = [
    "This is a read-only Thin Judge. Synthesize only the admitted planning, candidate verification, residual-gap, waiver, and closure-fact refs supplied in the briefing.",
    "Do not edit the product tree, rerun tests/builds, create gap tasks, or mutate workflow truth. Missing or unreadable admitted refs are a blocked result, not permission to inspect the product from scratch.",
    "Return one machine-readable top-level `goal_closure_result` with schema_version `goal-closure-result.v1`; bind workflow_run_id, goal_id, flow_kind, task_map_generation, target_commit, objective_ref, goal_claim_set ref/digest, planning/candidate refs, closure fact ref/digest, and every canonical goal claim.",
    "Use verdict passed only when every mandatory claim is closed or explicitly waived with admitted supporting refs. Use rejected for semantic gaps and blocked for external/human or unavailable-input blockers.",
    "A semantic rejected/blocked verdict is still a successfully executed Judge call: emit the configured child success event so the Kernel semantic router, not fanout retry, owns recovery.",
]

_REFACTOR_SCAN_INSTRUCTIONS = [
    "This is the initial refactor scan stage, not parity completion verification.",
    "Missing target implementation is expected before impl; convert it into source inventory, capability/parity gaps, risks, and planning inputs.",
    "Emit success when the assigned source/scope has been inspected and an evidence-backed scan report can feed the plan stage.",
    "Emit failure only when the assigned scope cannot be inspected or the scan report cannot be produced.",
]

_REFACTOR_PLAN_INSTRUCTIONS = [
    "Synthesize the scan reports into a refactor plan, task_map, gates, and risk register.",
    "Do not re-scan from scratch; use child scan artifacts and preserve source anchors/evidence refs for each task.",
    "Emit success only when downstream impl lanes can consume the task_map without inventing missing scope.",
    *_TASK_MAP_CONTRACT_INSTRUCTIONS,
]

_REFACTOR_VERIFY_BRIDGE_INSTRUCTIONS = [
    "This bridge runs after candidate tests pass; inspect whether enough evidence exists to trigger post-verify parity scanning.",
    "Do not reject merely because parity scan has not run yet; request/enable the configured parity scan unless tests or candidate evidence are invalid.",
    "A passing report must include a non-empty top-level `evidence_refs` list pointing to the candidate commit/ref and concrete test event, report, or command artifact; findings and prose summaries do not substitute for evidence refs.",
]

_REFACTOR_PARITY_SCAN_INSTRUCTIONS = [
    "This is post-verify parity discovery. Compare the delivered candidate with the original source/plan and report residual module gaps.",
    "Emit success when parity evidence closes the configured threshold or produces a bounded gap plan; emit failure for blocking unclosed P0/P1 gaps.",
]


def _pick(raw: dict, *names: str, default: Any = None) -> Any:
    for name in names:
        if name in raw:
            return raw[name]
    return default


def _mapping_param(raw: Any, *, name: str) -> dict[str, Any]:
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise WorkflowProfileError(f"{name}: must be a mapping")
    return dict(raw)


_ROLE_DEFAULT_ALIASES = {
    "permissionMode": "permission_mode",
    "stuckThresholdSeconds": "stuck_threshold_seconds",
    "spawnReadyTimeoutSeconds": "spawn_ready_timeout_seconds",
    "contextWarningThreshold": "context_warning_threshold",
    "contextCompactThreshold": "context_compact_threshold",
    "contextHardCap": "context_hard_cap",
    "drainHoldSeconds": "drain_hold_seconds",
    "budgetUsd": "budget_usd",
}

_ROLE_DEFAULT_FIELDS = (
    "transport",
    "stuck_threshold_seconds",
    "spawn_ready_timeout_seconds",
    "context_warning_threshold",
    "context_compact_threshold",
    "context_hard_cap",
    "drain_hold_seconds",
    "budget_usd",
)

_LANE_ROLE_DEFAULT_FIELDS = (
    "stuck_threshold_seconds",
    "spawn_ready_timeout_seconds",
    "budget_usd",
)


def _role_defaults_param(raw: Any, *, name: str) -> dict[str, Any]:
    """Normalize the external roleDefaults surface before flow expansion."""
    values = _mapping_param(raw, name=name)
    normalized: dict[str, Any] = {}
    for key, value in values.items():
        source = str(key)
        target = _ROLE_DEFAULT_ALIASES.get(source, source)
        if any(char.isupper() for char in source) and source not in _ROLE_DEFAULT_ALIASES:
            raise WorkflowProfileError(
                f"{name}: unknown camelCase key {source!r}"
            )
        if target in normalized and normalized[target] != value:
            raise WorkflowProfileError(
                f"{name}: conflicting values for {target!r}"
            )
        normalized[target] = value
    return normalized


def _lane_role_template(
    *,
    backend: str,
    role_defaults: dict[str, Any],
) -> dict[str, Any]:
    template: dict[str, Any] = {
        "backend": backend,
        "permission_mode": str(role_defaults.get("permission_mode") or "bypass"),
        "skills_by_stage": {},
    }
    for field in _LANE_ROLE_DEFAULT_FIELDS:
        if field in role_defaults:
            template[field] = role_defaults[field]
    return template


def _list_param(raw: Any, *, name: str) -> list[str]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise WorkflowProfileError(f"{name}: must be a list")
    return [str(item) for item in raw if str(item).strip()]


def _refactor_contract_payload(*, lanes: int, assembly: Any) -> dict[str, Any]:
    assembly_task_id = ""
    assembly_policy = "none"
    if isinstance(assembly, dict):
        assembly_task_id = str(assembly.get("task") or "").strip()
        assembly_policy = "declared_task"
    elif str(assembly).strip().lower() != "none":
        assembly_policy = "declared_task"
        assembly_task_id = str(assembly).strip()
    return {
        "schema_version": "refactor-plan-contract.v1",
        "lane_count": lanes,
        "assembly": assembly,
        "assembly_policy": assembly_policy,
        "assembly_task_id": assembly_task_id,
        "task_map_rule": (
            "If assembly_policy=declared_task, task_map.tasks must include "
            "assembly_task_id or one task with root_owner_class='assembly'. "
            "If assembly_policy=none, a single serial task map may omit "
            "assembly, but tasks still need explicit owned paths and source "
            "anchors. Set workspace_root_owner_required=true only when the "
            "plan must change or validate a root-level scaffold/entrypoint; "
            "otherwise omit it or set false."
        ),
    }


def expand_refactor_flow_v1(params: dict) -> dict[str, Any]:
    """refactor-flow/v1 → {roles, stages, pipelines, schema_profile}。

    参数面(全部可省,缺省即 hermes 形):lanes(5)/assembly(必给,
    doc 88 V1 教训)/budgets{maxReworkAttempts,traceBudget}/
    laneRoleTemplate/entryTrigger(refactor.scan.requested)/
    scan{children:[{id,instructionRef}],synthRole}/judgeRole。
    """
    unknown = sorted(str(k) for k in params if str(k) not in _KNOWN_PARAM_KEYS)
    if unknown:
        raise WorkflowProfileError(
            f"refactor-flow/v1: unknown param(s) {unknown} (fail-closed)"
        )
    lanes = int(_pick(params, "lanes", "laneCount", "lane_count", default=5))
    assembly = params.get("assembly")
    if assembly is None:
        raise WorkflowProfileError(
            "refactor-flow/v1: assembly is required (doc 88 §3.2 R21/R24 "
            "无主地带教训;显式 {task: <id>} 或 'none')"
        )
    budgets = params.get("budgets") or {}
    max_rework = int(_pick(
        budgets, "maxReworkAttempts", "max_rework_attempts", default=2,
    ))
    trace_budget = int(_pick(budgets, "traceBudget", "trace_budget", default=6))
    entry = str(_pick(
        params, "entryTrigger", "entry_trigger",
        default="refactor.scan.requested",
    ))
    judge_role = str(_pick(params, "judgeRole", "judge_role",
                           default="judge-refactor"))
    scan_cfg = params.get("scan") or {}
    scan_children = scan_cfg.get("children") or [
        {"id": cid} for cid in _SCAN_CHILDREN_V1
    ]
    scan_synth = str(scan_cfg.get("synthRole")
                     or scan_cfg.get("synth_role") or "refactor-plan-synth")
    refactor_contract = _refactor_contract_payload(
        lanes=lanes,
        assembly=assembly,
    )

    # ---- scan / plan 段(canonical stages,候选级链的前半) ----
    children_cfg = []
    scan_roles = []
    for child in scan_children:
        cid = str(child.get("id") or "").strip()
        if not cid:
            raise WorkflowProfileError("scan.children[].id is required")
        payload: dict[str, Any] = {"child_id": cid}
        for key in ("instruction", "instruction_ref", "instructionRef"):
            if child.get(key):
                payload["instruction_ref" if key != "instruction" else key] = (
                    str(child[key])
                )
        children_cfg.append({"role_instance": cid, "payload": payload})
        scan_roles.append(cid)

    stages = [
        {
            "id": "flow-scan",
            "trigger": entry,
            "topology": "fanout_reader",
            "roles": scan_roles,
            "target_ref": "",
            "fanout": {"children": children_cfg},
            "criteria": {"instructions": _REFACTOR_SCAN_INSTRUCTIONS},
            "aggregate": {
                "mode": "wait_for_all",
                "success_event": "zaofu.refactor.review.ready",
                # ZF-E2E-PRDCTL-P2-7-1:scan 与 plan 曾共用 plan.blocked——
                # kernel failure→stage 先到先得,scan 失败会被 replan 成
                # 重跑 plan(深水无界环同族)。
                "failure_event": "zaofu.refactor.scan.blocked",
            },
        },
        {
            "id": "flow-plan",
            "trigger": "zaofu.refactor.review.ready",
            "topology": "fanout_reader",
            "roles": [scan_synth],
            "fanout": {
                "children": [{
                    "role_instance": scan_synth,
                    "payload": {
                        "child_id": scan_synth,
                        "refactor_contract": refactor_contract,
                    },
                }],
            },
            "criteria": {"instructions": _REFACTOR_PLAN_INSTRUCTIONS},
            "aggregate": {
                "mode": "wait_for_all",
                "success_event": "zaofu.refactor.plan.ready",
                "failure_event": "zaofu.refactor.plan.blocked",
            },
        },
    ]

    # ---- lane 段:复用 doc 88 原语(G3 物化 impl→review→verify→judge) ----
    pipeline = {
        "id": "flow-lanes",
        "kind": "lane_pipeline",
        "trigger": "task_map.ready",
        "affinity_key": "affinity_tag",
        "lane_count": lanes,
        "max_rework_attempts": max_rework,
        "trace_budget": trace_budget,
        "assembly": assembly,
        "schema_profile": "refactor-flow/v1",
        "stages": [
            {"id": "impl", "role_pattern": "dev-lane-{lane}"},
            {"id": "review",
             "on_failure": {"rework_to": "impl", "feedback_artifact": "required"}},
            {"id": "verify",
             "on_failure": {"rework_to": "impl", "feedback_artifact": "required"}},
        ],
        "final": {
            "when": "all_tasks_verified",
            "role": judge_role,
            "success": "judge.passed",
            "failure": "judge.failed",
        },
    }
    template = _pick(params, "laneRoleTemplate", "lane_role_template")
    if template:
        pipeline["lane_role_template"] = template

    # ---- 配套 roles(scan 三件 + synth + judge;lane roles 由模板生成) ----
    roles = [
        *({"name": r, "instance_id": r, "role_kind": "reader",
           "backend": str((template or {}).get("backend") or "claude-code"),
           "permission_mode": "bypass"}
          for r in (*scan_roles, scan_synth, judge_role)),
    ]
    return {
        "roles": roles,
        "stages": stages,
        "pipelines": [pipeline],
        # 入口事件 = 外部接口(operator/上游系统 emit),graph producer
        # 判定豁免(loader merge 进 dag.external_triggers)。
        "external_triggers": [entry, "task_map.ready"],
        "schema_profile": str(_pick(
            params, "schemaProfile", "schema_profile",
            default="refactor-flow/v1",
        )),
    }


def expand_refactor_flow_v3(params: dict) -> dict[str, Any]:
    """refactor-flow/v3 → goal convergence loop shape.

    v3 is intentionally still a compiler output to canonical stages/pipeline:
    runtime keeps consuming ``ZfConfig`` only.  The generated shape is:

    scan -> plan -> impl/verify per-lane pipeline -> verify.passed bridge
      -> module parity scan -> module.parity.closed -> final judge

    Gap task admission is handled by the existing deterministic
    ``gap_plan.ready -> task_map.amended -> task_map.ready`` bridge.
    """
    unknown = sorted(str(k) for k in params if str(k) not in _KNOWN_PARAM_KEYS_V3)
    if unknown:
        raise WorkflowProfileError(
            f"refactor-flow/v3: unknown param(s) {unknown} (fail-closed)"
        )
    lanes = int(_pick(params, "lanes", "laneCount", "lane_count", default=5))
    assembly = params.get("assembly")
    if assembly is None:
        raise WorkflowProfileError(
            "refactor-flow/v3: assembly is required (explicit {task: <id>} "
            "or 'none')"
        )
    budgets = params.get("budgets") or {}
    max_rework = int(_pick(
        budgets, "maxReworkAttempts", "max_rework_attempts", default=2,
    ))
    trace_budget = int(_pick(budgets, "traceBudget", "trace_budget", default=8))
    entry = str(_pick(
        params, "entryTrigger", "entry_trigger",
        default="refactor.scan.requested",
    ))
    judge_role = str(_pick(params, "judgeRole", "judge_role",
                           default="judge-refactor"))
    verify_bridge_role = str(_pick(
        params, "verifyBridgeRole", "verify_bridge_role",
        default="refactor-verify-bridge",
    ))
    module_parity_role = str(_pick(
        params, "moduleParityRole", "module_parity_role",
        default="module-parity-scan",
    ))
    role_defaults = _role_defaults_param(
        _pick(params, "roleDefaults", "role_defaults"),
        name="refactor-flow/v3.roleDefaults",
    )
    role_skill_bundles = _mapping_param(
        _pick(params, "roleSkillBundles", "role_skill_bundles"),
        name="refactor-flow/v3.roleSkillBundles",
    )
    scan_cfg = params.get("scan") or {}
    scan_children = scan_cfg.get("children") or [
        {"id": cid} for cid in _SCAN_CHILDREN_V1
    ]
    scan_synth = str(scan_cfg.get("synthRole")
                     or scan_cfg.get("synth_role") or "refactor-plan-synth")
    refactor_contract = _refactor_contract_payload(
        lanes=lanes,
        assembly=assembly,
    )
    backend = str(
        role_defaults.get("backend")
        or ((params.get("laneRoleTemplate") or params.get("lane_role_template") or {})
         .get("backend"))
        or "claude-code"
    )

    children_cfg = []
    scan_roles = []
    for child in scan_children:
        cid = str(child.get("id") or "").strip()
        if not cid:
            raise WorkflowProfileError("scan.children[].id is required")
        payload: dict[str, Any] = {"child_id": cid}
        for key in ("instruction", "instruction_ref", "instructionRef"):
            if child.get(key):
                payload["instruction_ref" if key != "instruction" else key] = (
                    str(child[key])
                )
        children_cfg.append({"role_instance": cid, "payload": payload})
        scan_roles.append(cid)

    stages = [
        {
            "id": "flow-scan",
            "trigger": entry,
            "topology": "fanout_reader",
            "roles": scan_roles,
            "target_ref": str(_pick(params, "targetRef", "target_ref", default="")),
            "fanout": {"children": children_cfg},
            "criteria": {"instructions": _REFACTOR_SCAN_INSTRUCTIONS},
            "aggregate": {
                "mode": "wait_for_all",
                "success_event": "zaofu.refactor.review.ready",
                # ZF-E2E-PRDCTL-P2-7-1:scan 与 plan 曾共用 plan.blocked——
                # kernel failure→stage 先到先得,scan 失败会被 replan 成
                # 重跑 plan(深水无界环同族)。
                "failure_event": "zaofu.refactor.scan.blocked",
            },
        },
        {
            "id": "flow-plan",
            "trigger": "zaofu.refactor.review.ready",
            "topology": "fanout_reader",
            "roles": [scan_synth],
            "fanout": {
                "children": [{
                    "role_instance": scan_synth,
                    "payload": {
                        "child_id": scan_synth,
                        "refactor_contract": refactor_contract,
                    },
                }],
            },
            "criteria": {"instructions": _REFACTOR_PLAN_INSTRUCTIONS},
            "aggregate": {
                "mode": "wait_for_all",
                "success_event": "zaofu.refactor.plan.ready",
                "failure_event": "zaofu.refactor.plan.blocked",
            },
        },
        {
            "id": "flow-verify-bridge",
            "trigger": "test.passed",
            "topology": "fanout_reader",
            "roles": [verify_bridge_role],
            "criteria": {"instructions": _REFACTOR_VERIFY_BRIDGE_INSTRUCTIONS},
            "aggregate": {
                "mode": "wait_for_all",
                "success_event": "verify.passed",
                "failure_event": "verify.failed",
                "child_success_event": "verify.bridge.child.completed",
                "child_failure_event": "verify.bridge.child.failed",
            },
        },
        {
            "id": "flow-module-parity-scan",
            "trigger": MODULE_PARITY_SCAN_REQUESTED,
            "topology": "fanout_reader",
            "roles": [module_parity_role],
            "criteria": {"instructions": _REFACTOR_PARITY_SCAN_INSTRUCTIONS},
            "aggregate": {
                "mode": "wait_for_all",
                "success_event": MODULE_PARITY_SCAN_COMPLETED,
                "failure_event": MODULE_PARITY_SCAN_FAILED,
                "child_success_event": "module.parity.child.completed",
                "child_failure_event": "module.parity.child.failed",
            },
        },
        {
            "id": "flow-final-judge",
            "trigger": "module.parity.closed",
            "topology": "fanout_reader",
            "roles": [judge_role],
            "criteria": {"instructions": _FINAL_JUDGE_INSTRUCTIONS},
            "aggregate": {
                "mode": "wait_for_all",
                "success_event": GOAL_CLOSURE_SYNTHESIZED,
                "failure_event": GOAL_CLOSURE_SYNTHESIS_FAILED,
                "child_success_event": "judge.child.completed",
                "child_failure_event": "judge.child.failed",
            },
        },
    ]

    template = dict(_pick(params, "laneRoleTemplate", "lane_role_template") or {})
    template.setdefault("backend", backend)
    template.setdefault(
        "permission_mode",
        str(role_defaults.get("permission_mode") or "bypass"),
    )
    for field in _LANE_ROLE_DEFAULT_FIELDS:
        if field in role_defaults:
            template.setdefault(field, role_defaults[field])
    if role_skill_bundles:
        skills_by_stage = dict(template.get("skills_by_stage") or {})
        for stage_id in ("impl", "verify"):
            if stage_id not in skills_by_stage and stage_id in role_skill_bundles:
                skills_by_stage[stage_id] = _list_param(
                    role_skill_bundles.get(stage_id),
                    name=f"refactor-flow/v3.roleSkillBundles.{stage_id}",
                )
        if skills_by_stage:
            template["skills_by_stage"] = skills_by_stage

    pipeline = _flow_kernel_lane_pipeline(
        pipeline_id="flow-lanes",
        lanes=lanes,
        impl_pattern="dev-lane-{lane}",
        verify_pattern="verify-lane-{lane}",
        lane_template=template,
        schema_profile=str(_pick(
            params, "schemaProfile", "schema_profile",
            default="refactor-flow/v3",
        )),
        assembly=assembly,
        max_rework_attempts=max_rework,
        trace_budget=trace_budget,
        # Refactor v3 must judge only after the module-parity loop closes.
        # A lane-pipeline final here would also mint flow-lanes-final on
        # test.passed, causing two judge.passed events and a second auto-ship
        # attempt before/after flow-final-judge.
        final={},
    )

    def _generated_role(name: str) -> dict[str, Any]:
        role = {
            "name": name,
            "instance_id": name,
            "role_kind": "reader",
            "backend": backend,
            "permission_mode": str(role_defaults.get("permission_mode") or "bypass"),
        }
        for field in _ROLE_DEFAULT_FIELDS:
            if field in role_defaults:
                role[field] = role_defaults[field]
        skills = _list_param(
            role_skill_bundles.get(name),
            name=f"refactor-flow/v3.roleSkillBundles.{name}",
        )
        if skills:
            role["skills"] = skills
        return role

    roles = [
        *(_generated_role(r)
          for r in (*scan_roles, scan_synth, verify_bridge_role,
                    module_parity_role, judge_role)),
    ]
    metadata = {
        "flow_kind": "refactor",
        "objective_ref": str(_pick(
            params, "objectiveRef", "objective_ref",
            default="",
        )),
        "source_root": str(_pick(
            params, "sourceRoot", "source_root",
            default="",
        )),
        "target_root": str(_pick(
            params, "targetRoot", "target_root",
            default="",
        )),
        "goal_profile": str(_pick(
            params, "goalProfile", "goal_profile",
            default="replace_existing_system",
        )),
        "quality_floor": str(_pick(
            params, "qualityFloor", "quality_floor",
            default="release_candidate",
        )),
        "gap_loop": str(_pick(params, "gapLoop", "gap_loop", default="enabled")),
        "verify_rescan": str(_pick(
            params, "verifyRescan", "verify_rescan",
            default="module_parity",
        )),
        "post_verify_discovery": str(_pick(
            params, "postVerifyDiscovery", "post_verify_discovery",
            default=str(_pick(
                params, "verifyRescan", "verify_rescan",
                default="module_parity",
            )),
        )),
        "completion_threshold": str(_pick(
            params, "completionThreshold", "completion_threshold",
            default="close_p0_p1",
        )),
        "parity_scope": list(_pick(
            params, "parityScope", "parity_scope",
            default=[],
        ) or []),
        "evidence_policy": str(_pick(
            params, "evidencePolicy", "evidence_policy",
            default="",
        )),
        "environment_policy": str(_pick(
            params, "environmentPolicy", "environment_policy",
            default="",
        )),
        "projection_policy": str(_pick(
            params, "projectionPolicy", "projection_policy",
            default="",
        )),
        "delivery_policy": str(_pick(
            params, "deliveryPolicy", "delivery_policy",
            default="report_only",
        )),
        "result_protocol": {"mode": "blocking"},
    }
    return {
        "roles": roles,
        "stages": stages,
        "pipelines": [pipeline],
        "external_triggers": [entry, "task_map.ready"],
        "schema_profile": "refactor-flow/v3",
        "metadata": metadata,
    }


def _controller_role(
    name: str,
    *,
    backend: str,
    role_kind: str,
    role_defaults: dict[str, Any],
    skills: list[str] | None = None,
) -> dict[str, Any]:
    role = {
        "name": name,
        "instance_id": name,
        "role_kind": role_kind,
        "backend": backend,
        "permission_mode": str(role_defaults.get("permission_mode") or "bypass"),
    }
    for field in _ROLE_DEFAULT_FIELDS:
        if field in role_defaults:
            role[field] = role_defaults[field]
    if skills:
        role["skills"] = list(skills)
    return role


def _role_skills(bundles: dict[str, Any], name: str) -> list[str]:
    return _list_param(
        bundles.get(name),
        name=f"flow.roleSkillBundles.{name}",
    )


def _flow_kernel_lane_pipeline(
    *,
    pipeline_id: str,
    lanes: int,
    impl_pattern: str,
    verify_pattern: str,
    lane_template: dict[str, Any],
    schema_profile: str,
    assembly: Any = "none",
    task_map_ref: str = "",
    max_rework_attempts: int = 2,
    trace_budget: int = 6,
    final: dict[str, Any] | None = None,
    stage_transition: str = "per_lane",
    final_barrier: str | None = None,
) -> dict[str, Any]:
    """Common post-task-map lane kernel shared by Issue/PRD/Refactor flows."""

    if final_barrier is None:
        final_barrier = "all_lanes_verified" if stage_transition == "per_lane" else ""
    barriers: dict[str, Any] = {"stage_transition": stage_transition}
    if final_barrier:
        barriers["final"] = final_barrier

    pipeline: dict[str, Any] = {
        "id": pipeline_id,
        "kind": "lane_pipeline",
        "trigger": "task_map.ready",
        "affinity_key": "affinity_tag",
        "lane_count": lanes,
        "overflow": "first_released_lane",
        "max_rework_attempts": max_rework_attempts,
        "trace_budget": trace_budget,
        "assembly": assembly,
        "schema_profile": schema_profile,
        "barriers": barriers,
        "lane_role_template": lane_template,
        "stages": [
            {
                "id": "impl",
                "role_pattern": impl_pattern,
                # Writer fanout briefing and runtime completion adoption use
                # dev.build.done/dev.failed as the authoritative writer
                # terminal events, regardless of lane role naming.
                "terminal": {"success": "dev.build.done", "failure": "dev.failed"},
            },
            {
                "id": "verify",
                "role_pattern": verify_pattern,
                "on_failure": {
                    "rework_to": "impl",
                    "feedback_artifact": "required",
                },
            },
        ],
        "final": dict(final or {}),
    }
    if task_map_ref:
        pipeline["task_source"] = {"task_map_ref": task_map_ref}
    return pipeline


def _controller_metadata(
    *,
    kind: str,
    params: dict[str, Any],
    default_quality: str,
    default_delivery: str,
    default_discovery: str = "",
) -> dict[str, Any]:
    return {
        "flow_kind": kind,
        "quality_floor": str(_pick(
            params, "qualityFloor", "quality_floor",
            default=default_quality,
        )),
        "evidence_policy": str(_pick(
            params, "evidencePolicy", "evidence_policy",
            default="strict_refs",
        )),
        "environment_policy": str(_pick(
            params, "environmentPolicy", "environment_policy",
            default="",
        )),
        "projection_policy": str(_pick(
            params, "projectionPolicy", "projection_policy",
            default="control_room",
        )),
        "delivery_policy": str(_pick(
            params, "deliveryPolicy", "delivery_policy",
            default=default_delivery,
        )),
        "post_verify_discovery": str(_pick(
            params, "postVerifyDiscovery", "post_verify_discovery",
            default=default_discovery,
        )),
        "result_protocol": {"mode": "blocking"},
    }


def _post_verify_discovery_stage(
    *,
    flow_kind: str,
    discovery_role: str,
    enabled: bool,
    trigger: str = "flow.discovery.requested",
) -> dict[str, Any] | None:
    if not enabled:
        return None
    return {
        "id": f"{flow_kind}-post-verify-discovery",
        "trigger": trigger,
        "topology": "fanout_reader",
        "roles": [discovery_role],
        "criteria": {"instructions": _POST_VERIFY_DISCOVERY_INSTRUCTIONS},
        "aggregate": {
            "mode": "wait_for_all",
            "success_event": "flow.discovery.completed",
            "failure_event": "flow.discovery.failed",
            "child_success_event": "flow.discovery.child.completed",
            "child_failure_event": "flow.discovery.child.failed",
        },
    }


def expand_issue_flow(params: dict) -> dict[str, Any]:
    """IssueFlow → minimal issue bugfix controller.

    The controller is intentionally small: triage creates/accepts the task map,
    writer lanes implement, verify lanes produce regression evidence, then a
    judge closes the issue. Runtime still consumes canonical stages only.
    """
    unknown = sorted(str(k) for k in params if str(k) not in _KNOWN_ISSUE_FLOW_KEYS)
    if unknown:
        raise WorkflowProfileError(
            f"IssueFlow: unknown param(s) {unknown} (fail-closed)"
    )
    lanes = int(_pick(params, "lanes", "laneCount", "lane_count", default=1))
    backend = str(_pick(params, "backend", default="codex"))
    topology = str(_pick(params, "topology", default="fanout")).strip() or "fanout"
    if topology not in {"fanout", "light"}:
        raise WorkflowProfileError(
            f"IssueFlow: unknown topology {topology!r} (fanout|light)"
        )
    if topology == "light":
        return _expand_product_flow_light(
            params,
            backend=backend,
            flow_kind="issue",
            entry_default="issue.requested",
            pipeline_id="issue-lanes",
            judge_role_default="judge-issue",
            impl_pattern_keys=("fixRolePattern", "fix_role_pattern"),
            impl_pattern_default="fix-lane-{lane}",
            objective_ref_keys=("issueRef", "issue_ref"),
            default_quality="issue-regression",
            default_delivery="report_only",
        )
    entry = str(_pick(
        params, "entryTrigger", "entry_trigger",
        default="issue.requested",
    ))
    task_map_ref = str(_pick(
        params, "taskMapRef", "task_map_ref",
        default="${task_map_ref}",
    ))
    triage_role = str(_pick(
        params, "triageRole", "triage_role",
        default="issue-triage",
    ))
    fix_pattern = str(_pick(
        params, "fixRolePattern", "fix_role_pattern",
        default="fix-lane-{lane}",
    ))
    verify_pattern = str(_pick(
        params, "verifyRolePattern", "verify_role_pattern",
        default="verify-lane-{lane}",
    ))
    discovery_role = str(_pick(
        params,
        "discoveryRole",
        "discovery_role",
        default="flow-discovery",
    ))
    judge_role = str(_pick(
        params, "judgeRole", "judge_role",
        default="judge-issue",
    ))
    role_defaults = _role_defaults_param(
        _pick(params, "roleDefaults", "role_defaults"),
        name="IssueFlow.roleDefaults",
    )
    bundles = _mapping_param(
        _pick(params, "roleSkillBundles", "role_skill_bundles"),
        name="IssueFlow.roleSkillBundles",
    )
    roles = [
        _controller_role(
            triage_role,
            backend=backend,
            role_kind="reader",
            role_defaults=role_defaults,
            skills=_role_skills(bundles, triage_role),
        ),
        _controller_role(
            discovery_role,
            backend=backend,
            role_kind="reader",
            role_defaults=role_defaults,
            skills=(
                _role_skills(bundles, discovery_role)
                or _role_skills(bundles, "discovery")
            ),
        ),
        _controller_role(
            judge_role,
            backend=backend,
            role_kind="reader",
            role_defaults=role_defaults,
            skills=_role_skills(bundles, judge_role),
        ),
    ]
    stages = [
        {
            "id": "issue-triage",
            "trigger": entry,
            "topology": "fanout_reader",
            "roles": [triage_role],
            "target_ref": str(_pick(params, "targetRef", "target_ref", default="HEAD")),
            "criteria": {"instructions": _ISSUE_TRIAGE_INSTRUCTIONS},
            "aggregate": {
                "mode": "wait_for_all",
                "success_event": "task_map.ready",
                "failure_event": "issue.triage.failed",
                "child_success_event": "issue.triage.child.completed",
                "child_failure_event": "issue.triage.child.failed",
            },
        },
    ]
    lane_template = _lane_role_template(
        backend=backend,
        role_defaults=role_defaults,
    )
    if _role_skills(bundles, "fix"):
        lane_template["skills_by_stage"]["impl"] = _role_skills(bundles, "fix")
    if _role_skills(bundles, "verify"):
        lane_template["skills_by_stage"]["verify"] = _role_skills(bundles, "verify")
    pipeline = _flow_kernel_lane_pipeline(
        pipeline_id="issue-lanes",
        lanes=lanes,
        impl_pattern=fix_pattern,
        verify_pattern=verify_pattern,
        lane_template=lane_template,
        schema_profile="canonical-dag/v6",
        task_map_ref=task_map_ref,
        final={
            "when": "all_tasks_verified",
            "role": judge_role,
            "trigger": "flow.goal.closed",
            "success": GOAL_CLOSURE_SYNTHESIZED,
            "failure": GOAL_CLOSURE_SYNTHESIS_FAILED,
        },
    )
    metadata = _controller_metadata(
        kind="issue",
        params=params,
        default_quality="issue-regression",
        default_delivery="report_only",
        default_discovery="regression_impact",
    )
    discovery_stage = _post_verify_discovery_stage(
        flow_kind="issue",
        discovery_role=discovery_role,
        enabled=bool(str(metadata.get("post_verify_discovery") or "").strip()),
    )
    if discovery_stage:
        stages.append(discovery_stage)
    metadata["issue_ref"] = str(_pick(params, "issueRef", "issue_ref", default=""))
    return {
        "roles": roles,
        "stages": stages,
        "pipelines": [pipeline],
        "external_triggers": [entry],
        "schema_profile": "canonical-dag/v6",
        "metadata": metadata,
    }


def _expand_prd_flow_light(params: dict, *, backend: str) -> dict[str, Any]:
    """批D(prd-goal e2e 复盘):小任务轻拓扑。

    textstat 实弹:编排固定成本 ≈ 实际工作量的 200%,"塞得进单上下文
    的任务"用全拓扑是净负资产。light = 单 lane goal 环 + 候选级
    verify + judge 三件套;scan/plan fanout 整段跳过,task_map 由
    kernel 在入口触发时机械合成单任务(runtime/light_flow.py),
    机械验收门(admission/F7/树哈希/judge)一个不拆。错判可免费
    升级全拓扑(topology: fanout)。
    """
    return _expand_product_flow_light(
        params,
        backend=backend,
        flow_kind="prd",
        entry_default="prd.requested",
        pipeline_id="prd-lanes",
        judge_role_default="judge-prd",
        impl_pattern_keys=("implRolePattern", "impl_role_pattern"),
        impl_pattern_default="dev-lane-{lane}",
        objective_ref_keys=("prdRef", "prd_ref"),
        default_quality="product-demo",
        default_delivery="report_and_demo",
    )


def _expand_product_flow_light(
    params: dict,
    *,
    backend: str,
    flow_kind: str,
    entry_default: str,
    pipeline_id: str,
    judge_role_default: str,
    impl_pattern_keys: tuple[str, ...],
    impl_pattern_default: str,
    objective_ref_keys: tuple[str, ...],
    default_quality: str,
    default_delivery: str,
) -> dict[str, Any]:
    entry = str(_pick(
        params, "entryTrigger", "entry_trigger", default=entry_default,
    ))
    judge_role = str(_pick(params, "judgeRole", "judge_role", default=judge_role_default))
    role_defaults = _role_defaults_param(
        _pick(params, "roleDefaults", "role_defaults"),
        name=f"{flow_kind}.roleDefaults",
    )
    bundles = _mapping_param(
        _pick(params, "roleSkillBundles", "role_skill_bundles"),
        name=f"{flow_kind}.roleSkillBundles",
    )
    roles = [
        _controller_role(
            judge_role,
            backend=backend,
            role_kind="reader",
            role_defaults=role_defaults,
            skills=_role_skills(bundles, judge_role),
        ),
    ]
    lane_template = _lane_role_template(
        backend=backend,
        role_defaults=role_defaults,
    )
    if _role_skills(bundles, "impl"):
        lane_template["skills_by_stage"]["impl"] = _role_skills(bundles, "impl")
    if _role_skills(bundles, "verify"):
        lane_template["skills_by_stage"]["verify"] = _role_skills(bundles, "verify")
    pipeline = _flow_kernel_lane_pipeline(
        pipeline_id=pipeline_id,
        lanes=1,
        impl_pattern=str(_pick(
            params, *impl_pattern_keys,
            default=impl_pattern_default,
        )),
        verify_pattern=str(_pick(
            params, "verifyRolePattern", "verify_role_pattern",
            default="verify-lane-{lane}",
        )),
        lane_template=lane_template,
        schema_profile="canonical-dag/v6",
        task_map_ref=str(_pick(
            params, "taskMapRef", "task_map_ref", default="${task_map_ref}",
        )),
        stage_transition="stage_barrier",
        final={
            "when": "all_tasks_verified",
            "role": judge_role,
            "trigger": "flow.goal.closed",
            "success": GOAL_CLOSURE_SYNTHESIZED,
            "failure": GOAL_CLOSURE_SYNTHESIS_FAILED,
        },
    )
    metadata = _controller_metadata(
        kind=flow_kind,
        params=params,
        default_quality=default_quality,
        default_delivery=default_delivery,
        default_discovery="",
    )
    metadata["topology"] = "light"
    metadata["light_entry_trigger"] = entry
    objective_ref = str(_pick(params, *objective_ref_keys, default=""))
    metadata["objective_ref"] = objective_ref
    metadata[f"{flow_kind}_ref"] = objective_ref
    metadata["target_root"] = str(_pick(params, "targetRoot", "target_root", default=""))
    return {
        "roles": roles,
        "stages": [],
        "pipelines": [pipeline],
        "external_triggers": [entry, "task_map.ready"],
        "schema_profile": "canonical-dag/v6",
        "metadata": metadata,
    }


def expand_prd_flow(params: dict) -> dict[str, Any]:
    """PrdFlow → minimal product-build controller."""
    unknown = sorted(str(k) for k in params if str(k) not in _KNOWN_PRD_FLOW_KEYS)
    if unknown:
        raise WorkflowProfileError(
            f"PrdFlow: unknown param(s) {unknown} (fail-closed)"
        )
    lanes = int(_pick(params, "lanes", "laneCount", "lane_count", default=2))
    backend = str(_pick(params, "backend", default="codex"))
    topology = str(_pick(params, "topology", default="fanout")).strip() or "fanout"
    if topology not in {"fanout", "light"}:
        raise WorkflowProfileError(
            f"PrdFlow: unknown topology {topology!r} (fanout|light)"
        )
    if topology == "light":
        return _expand_prd_flow_light(params, backend=backend)
    entry = str(_pick(
        params, "entryTrigger", "entry_trigger",
        default="prd.requested",
    ))
    task_map_ref = str(_pick(
        params, "taskMapRef", "task_map_ref",
        default="${task_map_ref}",
    ))
    scan_roles = _list_param(
        _pick(params, "scanRoles", "scan_roles", default=["product-scan", "tech-scan"]),
        name="PrdFlow.scanRoles",
    ) or ["product-scan", "tech-scan"]
    plan_role = str(_pick(params, "planRole", "plan_role", default="planner"))
    impl_pattern = str(_pick(
        params, "implRolePattern", "impl_role_pattern",
        default="dev-lane-{lane}",
    ))
    verify_pattern = str(_pick(
        params, "verifyRolePattern", "verify_role_pattern",
        default="verify-lane-{lane}",
    ))
    discovery_role = str(_pick(
        params,
        "discoveryRole",
        "discovery_role",
        default="flow-discovery",
    ))
    judge_role = str(_pick(params, "judgeRole", "judge_role", default="judge-prd"))
    role_defaults = _role_defaults_param(
        _pick(params, "roleDefaults", "role_defaults"),
        name="PrdFlow.roleDefaults",
    )
    bundles = _mapping_param(
        _pick(params, "roleSkillBundles", "role_skill_bundles"),
        name="PrdFlow.roleSkillBundles",
    )
    roles = [
        *(
            _controller_role(
                role,
                backend=backend,
                role_kind="reader",
                role_defaults=role_defaults,
                skills=_role_skills(bundles, "scan") or _role_skills(bundles, role),
            )
            for role in scan_roles
        ),
        _controller_role(
            plan_role,
            backend=backend,
            role_kind="reader",
            role_defaults=role_defaults,
            skills=_role_skills(bundles, plan_role),
        ),
        _controller_role(
            discovery_role,
            backend=backend,
            role_kind="reader",
            role_defaults=role_defaults,
            skills=(
                _role_skills(bundles, discovery_role)
                or _role_skills(bundles, "discovery")
            ),
        ),
        _controller_role(
            judge_role,
            backend=backend,
            role_kind="reader",
            role_defaults=role_defaults,
            skills=_role_skills(bundles, judge_role),
        ),
    ]
    stages = [
        {
            "id": "prd-scan",
            "trigger": entry,
            "topology": "fanout_reader",
            "roles": scan_roles,
            "target_ref": str(_pick(params, "targetRef", "target_ref", default="HEAD")),
            "criteria": {"instructions": _PRD_SCAN_INSTRUCTIONS},
            "aggregate": {
                "mode": "wait_for_all",
                "success_event": "prd.scan.completed",
                "failure_event": "prd.scan.failed",
                "child_success_event": "prd.scan.child.completed",
                "child_failure_event": "prd.scan.child.failed",
            },
        },
        {
            "id": "prd-plan",
            "trigger": "prd.scan.completed",
            "topology": "fanout_reader",
            "roles": [plan_role],
            "criteria": {"instructions": _PRD_PLAN_INSTRUCTIONS},
            "aggregate": {
                "mode": "wait_for_all",
                "success_event": "task_map.ready",
                "failure_event": "prd.plan.failed",
                "child_success_event": "prd.plan.child.completed",
                "child_failure_event": "prd.plan.child.failed",
            },
        },
    ]
    lane_template = _lane_role_template(
        backend=backend,
        role_defaults=role_defaults,
    )
    if _role_skills(bundles, "impl"):
        lane_template["skills_by_stage"]["impl"] = _role_skills(bundles, "impl")
    if _role_skills(bundles, "verify"):
        lane_template["skills_by_stage"]["verify"] = _role_skills(bundles, "verify")
    pipeline = _flow_kernel_lane_pipeline(
        pipeline_id="prd-lanes",
        lanes=lanes,
        impl_pattern=impl_pattern,
        verify_pattern=verify_pattern,
        lane_template=lane_template,
        schema_profile="canonical-dag/v6",
        task_map_ref=task_map_ref,
        final={
            "when": "all_tasks_verified",
            "role": judge_role,
            "trigger": "flow.goal.closed",
            "success": GOAL_CLOSURE_SYNTHESIZED,
            "failure": GOAL_CLOSURE_SYNTHESIS_FAILED,
        },
    )
    metadata = _controller_metadata(
        kind="prd",
        params=params,
        default_quality="product-demo",
        default_delivery="report_and_demo",
        default_discovery="product_completeness",
    )
    discovery_stage = _post_verify_discovery_stage(
        flow_kind="prd",
        discovery_role=discovery_role,
        enabled=bool(str(metadata.get("post_verify_discovery") or "").strip()),
    )
    if discovery_stage:
        stages.append(discovery_stage)
    metadata["prd_ref"] = str(_pick(params, "prdRef", "prd_ref", default=""))
    metadata["target_root"] = str(_pick(params, "targetRoot", "target_root", default=""))
    metadata["stack"] = str(_pick(params, "stack", default=""))
    return {
        "roles": roles,
        "stages": stages,
        "pipelines": [pipeline],
        "external_triggers": [entry],
        "schema_profile": "canonical-dag/v6",
        "metadata": metadata,
    }


WORKFLOW_PROFILES = {
    "refactor-flow/v1": expand_refactor_flow_v1,
    "refactor-flow/v3": expand_refactor_flow_v3,
}


def expand_workflow_profile(params: dict) -> dict[str, Any]:
    name = str(_pick(params, "flowProfile", "flow_profile", default=""))
    expander = WORKFLOW_PROFILES.get(name)
    if expander is None:
        raise WorkflowProfileError(
            f"unknown flow profile {name!r}; shipped: "
            f"{sorted(WORKFLOW_PROFILES)}"
        )
    return expander(params)


def merge_expansion_into_body(body: dict, expansion: dict) -> None:
    """展开产物并入 ZfConfig body —— 三源守门:手写最高。

    - roles:同名手写存在则跳过 profile role(手写覆盖);
    - stages:同 trigger 手写存在则跳过 + WARN(漂移提示,G3 同规则);
    - pipelines:append(lane 物化的守门在 G3);
    - dag.schema_profile:手写缺位才填。
    """
    roles = body.setdefault("roles", [])
    have = {str(r.get("name") or "") for r in roles if isinstance(r, dict)}
    for role in expansion["roles"]:
        if role["name"] not in have:
            roles.append(role)

    workflow = body.setdefault("workflow", {})
    stages = workflow.setdefault("stages", [])
    hand_triggers = {
        (
            str(s.get("trigger") or ""),
            str(s.get("flow_kind") or "").strip().lower(),
        )
        for s in stages if isinstance(s, dict)
    }
    for stage in expansion["stages"]:
        stage_key = (
            str(stage.get("trigger") or ""),
            str(stage.get("flow_kind") or "").strip().lower(),
        )
        if stage_key in hand_triggers:
            print(
                f"Warning: flowProfile stage {stage['id']!r} skipped — "
                f"hand-written stage already covers trigger "
                f"{stage['trigger']!r} (手写最高;doc 90 V2 三源守门)",
                file=sys.stderr,
            )
            continue
        stages.append(stage)
        hand_triggers.add(stage_key)

    workflow.setdefault("pipelines", []).extend(expansion["pipelines"])
    dag = workflow.setdefault("dag", {})
    if isinstance(dag, dict):
        if not dag.get("schema_profile"):
            dag["schema_profile"] = expansion["schema_profile"]
        ext = dag.setdefault("external_triggers", [])
        if isinstance(ext, list):
            for trig in expansion.get("external_triggers", []):
                if trig not in ext:
                    ext.append(trig)
    metadata = expansion.get("metadata") or {}
    if isinstance(metadata, dict) and metadata:
        flow_kind = str(expansion.get("flow_kind") or metadata.get("flow_kind") or "")
        if expansion.get("multi_kind") and flow_kind:
            metadata_by_kind = workflow.setdefault("_flow_metadata_by_kind", {})
            if isinstance(metadata_by_kind, dict):
                metadata_by_kind[flow_kind] = dict(metadata)
            routes = workflow.setdefault("kind_routes", {})
            if isinstance(routes, dict):
                route = routes.setdefault(flow_kind, {})
                if isinstance(route, dict):
                    route.setdefault("pattern_id", str(expansion.get("entry_stage_id") or ""))
                if flow_kind == "prd":
                    routes.setdefault("feat", {"alias": "prd"})
        else:
            flow_meta = workflow.setdefault("_flow_metadata", {})
            if isinstance(flow_meta, dict):
                flow_meta.update(metadata)

    # canonical-dag/v6 and refactor-flow/v3 terminate through the admitted
    # Thin Judge -> Completion Gate protocol. Keep hand-written goal settings
    # authoritative, but make the protocol runnable for a bare Flow document.
    if expansion.get("schema_profile") in {"canonical-dag/v6", "refactor-flow/v3"}:
        goal = body.setdefault("goal", {})
        if isinstance(goal, dict):
            goal.setdefault("enabled", True)
