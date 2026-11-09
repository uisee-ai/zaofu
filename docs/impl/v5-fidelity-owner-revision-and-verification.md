# V5 需求修订、审批装配与输入见证

来源：[真实链路失败记录](../records/2026-10-31-kanban-judge-fidelity-e2e.md)。
相关：[受控装配](registered-controller-assembly.md)、[验证上下文](v5-verification-efficiency.md)。

## Plan 引用修改的正式需求修订

Plan 引用 Agent 在既有局部 Markdown diff 外，返回 `contract_changes`：

- `requirement`：已绑定 Requirement 文本叶子的 `path/before/after`。
- `claims`：已绑定 Task Map 的 `goal_claim_id/before/after`。

语义判断由 Agent 和 `zf-requirement-reconciliation` 方法负责；纯措辞修改返回空列表。
UI 展示两种差异。用户 Apply 经原有 token-gated Plan reject 控制动作，在 Plan 决策锁内
复核待审版本、引用范围、旧正文和 Requirement/Task Map 摘要，写不可变新 Requirement
及 `plan-requirement-amendment.v1`，由唯一 `plan.rejected` 事件发布授权。
未 Apply 的草稿、Agent 自发事件、旧审批和其他 Run 的变更均不是授权。

不重写已经启动的 Workflow Request/Run 快照：它们是历史启动依据。当前 Run 的新需求
由受控 owner amendment 明确覆盖。Planner 的当前 Requirement 输入、新 Package 的
Requirement port、强制条款连续性和 Judge closure identity 都消费这条授权链；旧来源
仍保留在 amendment 和上一版本 Package 中。需求 acceptance 的 claim ID 跨文字修订稳定。
只能授权明确的文字 before/after；不能删除条款、关闭 mandatory 或改变验收责任字段。
下一版 Plan 仍需重新审核和批准，Apply 不直接启动 Impl。

现有 Markdown-only 需求继续支持文档引用修改；没有正式 Requirement JSON 时不能宣称
完成了结构化需求修订。新修订也不证明语义正确，Planner/Critic、独立测试与 Judge 仍需核验。

## 首次 YAML 装配保留人工审批要求

注册控制器 synthesis 明确输出：

```json
{"approval_requirements":{"plan":"required","source_quotes":["Plan Preview 需要人工确认"]}}
```

`plan` 取 `required` 或 `not_requested`。Agent 根据输入解释审批意图；代码核对结构和
逐字来源引用，并将 required 编译为 `workflow.plan_approval: true`。Proposal、Raw Agent
修订与 config Apply 均保护该约束。未请求关闭门禁时，not_requested 不覆盖模板现有门禁。
Kanban、Channel 和 CLI 经同一 synthesis/proposal 路径执行。Workflow YAML 的批准与
执行前 Plan 的批准仍是两次不同决定；有引用不代表代码能够证明 Agent 的语义判断正确。

## 输出见证与上下文

必读接口所有响应统一为 `required-artifact-page.v2`，不再返回小批次 JSONL。
每页带 `attempt_id`、`bundle_digest`、唯一 `delivery_id`、完整 `items` 与游标。
正文完全相同的别名只交付一次，但每个来源仍独立鉴权、校验摘要并记录收据。
调用方应原样返回单页，依次取完游标；不得过滤身份字段或在一个大输出中拼接全部页。

CLI 写入/flush 仅证明生产。Codex transcript 观察器只解码明确的工具结果包装，
可以识别 JSON 排版、字段顺序、Promise 结果数组变化；不接受丢字段、截断、旧 attempt、
旧读取标识或 arguments echo。见证保存 session、call、时间、原始输出与不可变引用。
已封存见证证明历史工具输出可观察，**不证明压缩后仍在当前上下文，也不证明模型理解**。
无法观察的 provider 或被过滤的历史输出仍是 unknown，不能据此判为丢失或通过。

## Review 材料与 Candidate Verify 宽度

有效空 decision register 的材料状态为 ready，pending/blocking 为 0。非空待决事项的数量
与材料状态分别显示；没有绑定、结构无效或摘要失败不会因空列表而变成 ready。
支持显式 JSON 登记和绑定 Plan 中有表头/分隔符的 Markdown decision register。
优先使用已绑定 Task Map 的结构化决策状态，避免从 Markdown 展示组合列猜测状态。

注册 blocking 控制器可选择 `parameters.candidate_verify_width`。装配为：

```yaml
taskPipeline:
  candidate:
    verifyWidth: 1
```

宽度必须为整数 1..已配置 lanes，不能为 0；省略时保留原宽度。编译器只缩小冻结候选
的静态 Verify fanout。Impl/Task Verify 池容量、独立角色要求、全局验收输入、验证命令、
Discovery、Judge 和失败门禁均保留。1/2/8 路通过现有调度与事件重放，不引入第二调度器。
Agent 按验收和风险选择宽度；宽度减少不等于证明 tokens 按比例减少，需真实用量对照。
## Reader 基础设施修复后重试

同一输入清单可包含名称相同、digest 不同的历史和当前 rework context。
`prepare_required_read` 按 source、artifact、digest 选择清单中已授权的精确版本；
未指定 digest 的多义手工请求拒绝，不能默认取第一项。

基础设施故障升级后，`stage-replan-new-generation` 的受控替代载荷为
`repaired_failure_event_id`、`approval_ref`、`reason`。Web 使用
`stage.replan.new_generation` 别名及既有 action token。内核验证配置中的 reader stage、
运行中 Run、最新因果 fanout、没有新派发/完成和没有重复恢复，再通过 EventWriter
追加新的 stage trigger。新 generation 仍执行 Planner/Critic 和原人工审批；沿用当前
正式 Requirement amendment。语义失败和已结束 Run 不接受此入口。

Run Manager 仅将匹配 task/lane 的进度作为旧告警的恢复证明。缺少作用域的人工升级
不能被任意 worker heartbeat/state 或其他 Run 的完成事件消除；需显式受控确认。


## Task pipeline 重试交接

交接投影也消费 Impl `task.dispatched` 中的 `rework_feedback.blocking_event_id`。
仅当引用现存待交接请求且 task/run/task_map_generation 精确匹配时确认送达；仍需
匹配 dispatch 的实现提交及独立验证目标才能关闭反馈。单独的实现完成、其他 Run
的通过、旧版本验证均不构成关闭。Run completion gate 从原事件账本重放复评，
不依赖 operator 补写成功事件；历史阻塞记录保留。
