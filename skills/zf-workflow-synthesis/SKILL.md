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

`plan_review.mode: none` 或 `plan_critic_enabled: false` 会从 PrdFlow 移除 Plan Critic。
`requested_roles` 必须对应修改后的有效角色，不机械复制 catalog 的默认名单，不能仍
请求 `plan-critic` 或其改名实例。Channel 的 PRD reviewer 与 Workflow Plan 角色分开。
targeted 保留 Critic 并给出明确问题；保留 Owner 要求的 Plan 审批，不以免审代表实施授权。

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


## PRD 的按需 Plan 评审

Owner 授权按风险选择评审时，在现有 short FlowSpec.parameters 中使用 `plan_review`：
`{mode: none|targeted|full, reason: "依据原始需求说明选择", focus: ["具名审查问题"]}`。
targeted 必须给 focus；none 不声明已审范围。普通 Flow 与当前 lanes 一起生成受控配置
候选；注册控制器保留其固定 lanes/pipeline，并绑定 controller_id/controller_digest。
不能直接修改运行状态、当前 zf.yaml 或跳过提案批准；不得降低原本明确要求的评审。

先判断已有独立审查覆盖了什么，再看本次 Plan 新增的任务拆分、共享产物、状态/异常、
交付时点与权限风险。低风险且边界明确可提议 none；局部影响明确用 targeted；原始
需求冲突、影响未知或高风险用 full。任务数量、文档长度不是风险判定依据。保留既有
审批和执行验收；机械检查先行，选读减量必须另满足运行时证据条件。未获得风险选择
授权时维持现有配置，不自动切换默认方式。兼容既有 plan_critic_enabled 开关，不能
与 plan_review.mode 给出矛盾设置。PrdFlow light 没有 Plan 阶段，不接收此策略。


## PRD 阶段按需分工

启用 OA 组队时，将分工写入同一需求修订的 `short_flow_spec.parameters.stage_teams`。
选择来自当前配置或所选控制器的已注册 reader 实例，在 `requested_roles` 中逐一列出。
根据尚待回答的问题说明 reason，保留已要求的独立评审、批准和验收。分别在 Plan、Task Verify 和 Judge 专项成员的委派合同中记录 `selection_reason` 和 `completion_condition`。

- `plan` 提供 `{reason, author, members}`。author 是本代唯一正式 Planner。每位专家填写
  `obligation_id`、`role`、`question`、`input_refs` 和 `expected_output`。输入引用限定
  在已有授权范围，每项交付给 author；可直接规划时给空 members。OA 亲自汇总时以本次 author 身份签署。
  对可能需要追加调查的在途扫描，可在 Proposal 的 `reserve_roles` 中预留独立 Reader，并纳入 `requested_roles`。只有已有成员提供新证据且通过 `request-fanout` 的增员准入后，才使用相应席位。
  引用项目实物时选择已授权的具体文件或索引文件。目录不能作为成员输入，也不会递归展开。
  指定章节时使用项目相对路径加唯一的 Markdown 标题锚点，例如 `docs/design.md#authentication`。
  默认成员必需。可选 Plan 专家声明 `required:false` 和 `coverage_owner`（本次 author），仍有正式回执。
  如果要改由 Planner 接手，在后继 Proposal 保留原成员和义务，添加 `disposition`，其 status 为
  `reassigned`，填写 reason、previous_proposal_digest 和 previous_run_id。核验原批准分工后再审批新版本。
  未核实的失败或取消仍是未交付。外部来源应引用授权后已落地的资料，输入中使用项目相对路径或证据标识。
- `task_verify` 提供 `{reason, mode: "oa_run_plan"}`，选择阻塞 pre_impl 检查点。等待已绑定的
  Task Map 和任务合同，再由 OA 在 Run Plan 中委派独立验证者。
- `candidate_verify` 提供 `{reason, width}`，选用已注册的并行 Reader 容量。冻结前将验收、
  必需用户旅程与全部已声明验收项分给具体 Reader。逐项列出跨任务检查 `integration_checks`，包含
  `check_id`、至少两个 `task_ids` 和可观察的 `assertion`，再用各 Reader 的 `integration_check_ids` 分派。
- `judge` 使用同样的 author/members 结构，author 从 `authorized_judges` 选择。专项审查者
  各自声明 `obligation_id`、`role`、`question`、`input_refs` 和 `expected_output`。默认意见必需，
  正式 Judge 等待这些签署。咨询意见设置 `required:false`，可与必需意见并行取证；正式 Judge
  可引用在 Discovery 发布前已准入的咨询意见。用 `objectives` 列明正式目标，用
  `questions: [{question_id, question}]` 逐条记录主审须裁决的分歧；无专项成员时同样适用。
  咨询缺席时仍可签署。主动撤回已获准的咨询义务时，后继 Proposal 保留原成员合同，
  用 `coverage_owner` 指向正式主审，`disposition` 记录 reassigned、原因与先前获批 Run/Proposal。
  主审从必读的 closure-fact 中取得本代目标、分歧与接手义务。
