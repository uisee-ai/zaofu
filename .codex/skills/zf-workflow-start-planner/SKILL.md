---
name: zf-workflow-start-planner
description: "Use when a ZaoFu operator, Kanban Agent, Channel member, or Coding Agent must classify a tracked Task, clarify missing workflow inputs, recommend one active workflow route, or create a durable workflow-start proposal through zf CLI. Do not use to bypass approval, invent workflow stages, or write runtime truth."
---

# ZaoFu Workflow Start Planner

## Objective

Turn one existing ZaoFu Task into an evidence-backed workflow route proposal.
The skill owns semantic recommendation. ZaoFu code owns route availability,
Task/config binding, proposal identity, approval, idempotency, and execution.

## Boundary

- Read `zf.yaml` only through the project and `zf workflow routes`; do not
  maintain a second route list.
- Never write `events.jsonl`, `kanban.json`, session files, or projections.
- Never start by arbitrary `pattern_id`; public proposals use catalog
  `route_id`.
- A proposal is not approval. Do not run `--apply` unless the operator has
  supplied an explicit approved proposal and the current capability allows it.
- Do not create a new topology from chat text. Use the workflow-config proposal
  path when no active route can satisfy the task.

## Method

1. Confirm that a real Task exists. If the user has not requested tracking,
   discuss the work normally. If they requested execution but no Task exists,
   propose `create-task` first.
2. Read the active choices:

   ```bash
   zf workflow routes --task TASK-ID --format json
   ```

3. Classify the goal semantically:
   - `delivery`: issue, PRD, feature, refactor, or implementation work;
   - `research`: evidence gathering before a product/delivery decision;
   - `review`: architecture, security, code, plan, or risk review;
   - `planning`: artifact or task-map production without implementation;
   - `general`: another explicitly registered route.
4. Remove routes that are unavailable or cannot satisfy required output.
5. Rank the remaining routes using the Task contract:
   - prefer a single lane for one bounded owner, narrow scope, and one direct
     verification path;
   - prefer multiple lanes when independent implementation surfaces, distinct
     expertise, or independent verification justify coordination cost;
   - use only roles and lane counts reported by the catalog;
   - prefer Research when a consequential decision lacks evidence;
   - prefer Delivery when acceptance and verification are already concrete.
6. If one missing answer materially changes the route or parameters, ask one
   Plan question with two or three mutually exclusive options. Put the
   recommended option first and allow a custom answer. Do not ask for secrets.
7. Once inputs are sufficient, create a durable proposal:

   ```bash
   zf workflow start \
     --task TASK-ID \
     --route ROUTE-ID \
     --objective "TASK-SPECIFIC OBJECTIVE" \
     --parameters-json '{"expected_output":"..."}' \
     --propose \
     --format json
   ```

8. Report the `proposal_event_id`, selected route, topology, roles, and unresolved
   risks. Do not claim that the Workflow started.

## Apply

Only an explicitly authorized operator process outside the provider session may
apply. The provider must not inspect, request, or receive
`ZF_WORKFLOW_ACTION_TOKEN`; return the proposal event id to the operator instead.

```bash
zf workflow start \
  --proposal-event-id EVENT-ID \
  --authorization-ref APPROVAL-REF \
  --authorization-token "$ZF_WORKFLOW_ACTION_TOKEN" \
  --apply \
  --format json
```

The operator host must configure `ZF_WORKFLOW_ACTION_TOKEN` without exporting it
into a Coding Agent or other provider process. Before apply, ensure the approval
refers to the exact proposal. Let the CLI reject missing/invalid authorization
and stale Task/config bindings. Never repair a rejection by editing runtime
truth or substituting a different route silently.

## Output Contract

For recommendation or clarification, include:

- Task id and concise goal classification;
- recommended `route_id` and why it fits;
- topology, roles, lane count, and expected output from the catalog;
- one alternative and its tradeoff when useful;
- missing parameter or blocker;
- proposal event id after `--propose`.

How to test: create a temporary Task, run `zf workflow routes`, propose
`research:fixed` or an active Delivery route, verify no
`workflow.invoke.requested` exists before apply, then apply the exact proposal.

## 未决 auto 与结果复用

`flow intake/classify --kind auto` 的关键词输出仅为候选提示，不能作为路线决定。
先按目标、约束与验收进行语义判断；否定句、引用示例和混合意图不能按关键词启动。
已有有效路由建议时复用其 Request/Requirement revision 和目录绑定；否则澄清或通过
既有 Workflow Synthesis Operation 给出建议。Provider 失败保持未决，不能默认为 issue。
显式有效路线不额外调用分类模型；旧请求按原版本恢复。

## 两类提交入口

- Kanban：从当前用户需求链或已有 Task 判断回答、澄清、仅跟踪还是执行。
  不强制要求 Issue／有界任务先生成 PRD；仅跟踪时不添加执行选项。
- Channel Group：只有 Owner 要求将确认产物转成任务／交付时才规划；保留当前
  Leader 身份与 revision、PRD ref/digest/revision、channel/thread 和批准范围。
  `PRD ready`、建单确认、配置批准和启动批准分别核对；不得推断前者自动授权后者。
- 用户在 Kanban 明确引用 Channel PRD 时仍使用相同来源检查；不因上下文里出现
  一个 PRD 就自动绑定。旧版本拒绝后不能用无来源的 direct 派发绕过检查。

## 需求初评与配置适配

结合完整验收、当前代码事实、模块依赖、共享写入、独立验证责任和资源约束评估。
说明依据，不只给“简单／困难”标签，也不根据关键词或文件数量决定路线。
区分 `flow_topology`、`lane_count`、`execution_policy`：一条 lane 仍可能是 fanout，
light 仍有实施／Verify／Judge；v4 `shadow` 与 `blocking` 不是相同执行策略。

先考虑回答／澄清／跟踪；执行时再比较单执行者、根 Agent 带助手和 Workflow。
只提出当前入口和 capability 已支持的可执行方案。E0 shadow、尚未注册的 Kimi、
没有强制隔离证据的 native delegation 不可宣称已启用；legacy direct 不是通用单 Agent。
用户指定的方式不可用时说明缺口，给有效替代或待处理选项，不静默换模式。

在现有 Plan `reason` 和选项 `description` 中保存：

1. 范围、依赖、风险和验证成本的事实来源，以及尚未确定的输入；
2. 推荐 route/profile、有效拓扑、资源取舍、预期产物及未选方案的原因；
3. 复用配置、绑定允许参数或需要配置变更的判断。

真实 Task 建立前只做初步推荐；由 Kernel 注入 Task ID 和配置／合同摘要。
大评估正文使用已有批准产物路径与 refs，禁止编造摘要或把必需正文只放在聊天摘要。
现有配置可满足时不重写 YAML；需要新增能力时走 workflow-config 提案，提供可审核
候选差异和验证方法。apply 后确认消费者有效版本，再重新预览启动。
没有 Task Map 时并发只是估计；后续由 Planner/Critic 复核真实依赖与 owned paths。
复杂任务不自动启用 canary；小任务不删独立验证或降级 blocking 验收。


### 规划完成后的明确启动授权

对于已确认、`readiness_verdict=ready` 但 `implementation_start=false` 的 Channel PRD，
后续 Owner 可以批准一份新的、精确绑定 Task／路线／配置的启动提案。
只有 Owner 当前明确要求执行时，才在该提案的 parameters 中设置
`authorize_channel_execution: true`，并在选项文字中写明“授权并启动此 Task”。
它不修改 PRD、验收或历史 readiness，也不等价于接受风险；Kernel 仍要求当前
Leader／PRD 绑定、Owner 或具备 `channel.workflow.start` 权限的委托人，以及未过期的
精确启动提案。仅确认 PRD、仅建单、存在未决 readiness 或原提案失效时不得使用此参数。
