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

# refactor-flow/v1 的 scan child 缺省三件套(id 即专长;指令正文住
# skills/PRD,经 instruction_ref 在 briefing 期物化 —— W1)。
_SCAN_CHILDREN_V1 = ("scan-contract", "scan-runtime", "scan-verification")


def _pick(raw: dict, *names: str, default: Any = None) -> Any:
    for name in names:
        if name in raw:
            return raw[name]
    return default


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
            "aggregate": {
                "mode": "wait_for_all",
                "success_event": "zaofu.refactor.review.ready",
                "failure_event": "zaofu.refactor.plan.blocked",
            },
        },
        {
            "id": "flow-plan",
            "trigger": "zaofu.refactor.review.ready",
            "topology": "fanout_reader",
            "roles": [scan_synth],
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
    if template is not None:
        pipeline["lane_role_template"] = template

    # ---- 配套 roles(scan 三件 + synth + judge;lane roles 由模板生成) ----
    roles = [
        *({"name": r, "instance_id": r, "role_kind": "reader",
           "backend": str((template or {}).get("backend") or "claude-code")}
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


WORKFLOW_PROFILES = {
    "refactor-flow/v1": expand_refactor_flow_v1,
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
        str(s.get("trigger") or "") for s in stages if isinstance(s, dict)
    }
    for stage in expansion["stages"]:
        if stage["trigger"] in hand_triggers:
            print(
                f"Warning: flowProfile stage {stage['id']!r} skipped — "
                f"hand-written stage already covers trigger "
                f"{stage['trigger']!r} (手写最高;doc 90 V2 三源守门)",
                file=sys.stderr,
            )
            continue
        stages.append(stage)

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
