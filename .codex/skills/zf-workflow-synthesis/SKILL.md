---
name: zf-workflow-synthesis
description: "Provider-neutral ZaoFu method for selecting IssueFlow, PrdFlow, RefactorFlow, or a registered Workflow from a confirmed Requirement and returning a short typed FlowSpec. Use only for Workflow Synthesis before Proposal compilation; do not decompose Tasks, edit zf.yaml, approve a Proposal, or start a Run."
---

# ZaoFu Workflow Synthesis

## Objective

Turn one confirmed Requirement into a small typed recommendation that the
deterministic Workflow Proposal compiler can admit. This is Flow selection,
not Planner Task decomposition and not Orchestrator recovery.

## Inputs

Read only the supplied current request revision, immutable Requirement,
controller catalog, adapter skill plan, registered roles/skills/profiles, and
project constraints. Treat every allowlist as closed.

## Method

1. Reject or ask a blocking question when objective, required target/source,
   acceptance criteria, or a decision-critical constraint is missing.
2. Select exactly one registered family:
   - `IssueFlow` for bounded diagnosis/repair.
   - `PrdFlow` for a product behavior or feature delivery.
   - `RefactorFlow` for source-to-target parity or structural migration.
   - `Workflow` only when the supplied catalog exposes the requested registered generic template and all of its required capabilities.
3. Produce only a short FlowSpec with purpose and the selected template's
   sanctioned parameters. Controller flows use `lanes`, `strictness`, and
   `pattern_id`; registered generic workflows use their catalog parameters.
4. Request only registered roles, skills, and execution profiles.
5. State rationale, assumptions, open questions, completion profile, and risk
   hints. Do not claim approval or execution.
6. Return exactly one JSON object matching `workflow-synthesis-result.v1`;
   emit no prose or code fence.

## Hard Boundaries

已注册 `bounded-agent-v1` 时，有界报告交付可用 `intent=bounded_execution`，参数为
`executor_role`、`verifier_role`、可选 `deadline_seconds`（1..3600，默认 600）。
完成要求为 `artifact_delivery`，必需产物 `agent-execute.result`。执行者和验证者
必须不同；提案编译还会检查角色原生协作禁用及资源上限。不得将此模板用于源码写入。

- Never emit expanded ZfConfig, arbitrary handlers, custom runtime code, Task
  objects, Task Map, approval, apply, submit, push, deploy, or terminal claims.
- Never modify `zf.yaml` or canonical state.
- Never invent ResearchFlow, custom composition, templates or operations outside the supplied catalog.
- A blocking open question means clarification, not a best-effort Proposal.
- Provider output may vary; the compiler digest and Kernel admission are the
  deterministic boundary.

## Output Shape

```json
{
  "schema_version": "workflow-synthesis-result.v1",
  "request_id": "current request id",
  "request_revision": 1,
  "requirement_ref": "exact supplied ref",
  "requirement_digest": "exact supplied digest",
  "selected_flow_family": "IssueFlow",
  "short_flow_spec": {
    "flow_family": "IssueFlow",
    "purpose": "bounded purpose",
    "parameters": {"lanes": 1, "strictness": "standard"}
  },
  "decision_rationale": "why this family fits",
  "assumptions": [],
  "open_questions": [],
  "requested_roles": [],
  "requested_skills": [],
  "requested_profiles": ["direct-v1"],
  "completion_profile": {
    "delivery_policy": "report_only",
    "completion_threshold": "",
    "required_artifacts": []
  },
  "risk_hints": []
}
```

## auto 输入与复用

Requirement 的 `kind=auto` 表示尚未作语义选路；不代表 IssueFlow，也不保证需要正式执行。
依据目标、约束和验收理解否定、引用与混合意图。信息不足或仅需解释时返回阻塞问题，
不能为了满足输出形状直接生成可执行提案；临时 family 候选必须保留 open_questions。
已有仍有效的同版本结果由宿主复用；调用失败不改为关键词规则。
