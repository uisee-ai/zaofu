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
   For an explicitly requested PRD v4 Task Pipeline, `task_pipeline` may contain
   exactly `profile` (`prd-flow-v4-task-pipeline`), `mode` (`shadow` or
   `blocking`), `impl_capacity`, `verify_capacity`, and
   `max_active_task_pipelines`. Set `lanes` to at least the larger pool
   capacity. Preserve independent verification; ask about decision-critical
   mode or limits rather than guessing. Shadow is observation, not execution.
   This produces a reviewed config candidate, never an applied config or Run.
   若用户指定目录 `registered_controllers` 中的完整版本（例如 V5），使用
   `parameters.controller_id` 和对应的 `controller_digest`，并给出适配当前项目验收的
   `verification_commands`。不要复制 YAML 或改写运行时状态；不要附带 `task_pipeline`
   或改变该控制器的 lanes。该选择进入完整配置替换的 Preview / Apply config / Approve & Run。
   编译器保留项目身份、状态目录、session 和本地 skill sources，绑定 Requirement 的项目内
   PRD/target 和当前 Git 分支；装配已安装控制器技能及内联 Profile，保留 V5 的阻塞门禁、
   独立验证、生命周期和预算。缺少业务路径/检查命令时问具体问题，不反问如何安装已注册控制器。
   聊天来源若没有单独的 `source_ref`，但已有 confirmed Requirement 和完整
   `task_input_binding`、`task_input_contract_ref/digest`，编译器会校验绑定并把完整
   Requirement（含 objective、澄清回答及来源）物化为项目内不可变输入。无需用户另造 PRD 文件；
   Task 输入投影本身不含完整 behavior，不可将其当成完整需求摘要。显式提供的源文件仍优先，
   源缺失、越界或绑定损坏必须修复，不能用此路径掩盖。
4. Request only registered roles, skills, and execution profiles.
   选中完整控制器时，使用该条目的 roles/skills/profiles 和 completion_profile；它们是待批准
   配置的能力，不要求先存在于当前项目。未选中时仍只使用当前配置的 allowlist。
   `allowed_skills` 表示已安装，不等于已装配；用 `configured_role_skills` 核对选中路线的实际
   角色绑定。确需新增能力时提出阻塞问题与配置变更要求，不能把希望使用的 skills 当作已启用。
5. State rationale, assumptions, open questions, completion profile, and risk
   hints. Do not claim approval or execution.
   注册控制器必须在 parameters.approval_requirements 显式输出
   `{"plan":"required|not_requested","source_quotes":["Requirement 原文"]}`。
   结合 objective、约束与澄清判断 Plan 人工确认需求；要求 Plan preview/approve 后才实施时
   选 required 并引用原文，不能继承模板 false。Workflow 配置批准与 Plan 批准是独立门禁。
   从 `configured_completion_by_family` 选择该路线可编译的完成要求，不能编造 threshold、
   required_artifacts 或把 `software_delivery` 改成通用模板的 `artifact_delivery`。
   “先预览、待确认后再启动”描述审批时序，不改变产品最终交付目标；YAML Preview 是准备工件，
   不属于该产品 Run 的完成条件。只有用户明确要求仅交付研究/文档时才选择对应文档路线。
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
- `direct-v1` 的 `access=read_only` 是注册执行策略合同中的兼容字段，不会把直接派发的
  writer 角色改成只读。判断直接实现能力时同时读取 `execution_catalog.roles` 的
  `role_kind`、`permission_mode` 和 Task 写入范围；不要建议 schema 不支持的
  `execution_profile.access=write_*`。Provider-native 策略仍按其严格只读能力边界处理。

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

## Candidate Verify 宽度

注册 blocking Task Pipeline 可通过 `parameters.candidate_verify_width` 选择 1..已注册 lanes 个独立 Candidate 验证者。根据验收范围、风险与实际可分工检查选择最小足够宽度；简单单任务通常为 1，确有独立检查维度再使用 2 或更多，并在 synthesis 解释理由。此参数只改变全局验证 fanout，不改变 Impl/Task Verify 池容量、验证命令、产品验收、Discovery 或 Judge 门禁。不得将减少宽度写成豁免验收。
