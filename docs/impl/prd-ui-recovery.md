# PRD UI 配置交接、回复恢复与 Owner 答复

本文落实 [PRD UI 实测](../records/2026-11-10-prd-ui-validation.md) 暴露的三个 P1/P2。
验证范围与保留失败见 [本轮验证记录](../records/2026-11-10-prd-ui-recovery-validation.md)。

## 配置应用与 consumer 交接

`workflow-config-apply` 在写入前比较执行策略摘要，并与 Run admission 共用锁。
在途 Run（包括可恢复 blocked Run）、未消费 invoke、operation、Channel 回复或活跃 Task
阻止替换执行策略，即使旧 runtime 已退出也不能用新配置覆盖其恢复上下文。
已取消的 Run 不因残留未消费 invoke 永久阻塞。纯展示配置不触发策略交接。

`apply_flow_submit` 按项目序列化，先校验已审批快照，再检查 watcher epoch 的 ready
回执与执行策略摘要。同策略复用；不同策略且项目空闲时，通过现有
`zf stop --fast --include-run-manager` / `zf start` 交接。新 watcher 确认策略后才发布
submit/Run/invoke。未就绪、停机或启动失败、跨 session、缺少旧版本回执都返回可重试
STOP，不先生成一个注定被拒绝的新 invoke。原 `consumer_config_stale` 准入保护仍保留。

`zf start` 的 ready 标记增加策略摘要与 tmux session；它是与 PID/epoch 绑定的进程投影，
不是新的业务状态机。Linux PID 探测识别 Z/X 进程，避免未回收 zombie 阻塞重启。
新模板保留项目原有 Run Manager / Autoresearch resident 配置，避免静默恢复默认值。

这是同项目空闲时的配置交接，不提供不同执行策略的多 Run 并行运行。旧版本 runtime
没有策略回执时，提示先执行受控 `zf restart`；不会猜测旧进程加载了哪份配置。

入口：`workflow_delivery.apply_flow_submit`、`WorkflowConfigApplyService.apply`、
`cli/start.py`。主要方法位于 `workflow_config_handoff.py`，既有 replay 状态展示方法
归入 `workflow_delivery_replay.py`，保持 submit 模块低于结构门禁上限。

## Channel 未知投递恢复

UI 在原失败回复上提供「核验终止并重试原回复」，调用现有 token-gated
`channel-drain-replies` action，附原 `channel_id`、`reconcile_request_id`、
`failure_event_id`、`run_generation`。服务端再次检查 Owner/delegate 权限、当前失败
与原请求身份；客户端不能提交终止判据、transcript 路径或自制回执。

当前支持 Codex 原生会话：服务器在自己的 CODEX_HOME 中定位唯一 session，核对项目
cwd、session、原派发时间窗口中的唯一 turn，以及它是 session 最后一个 turn。
只有该 turn 的确定 `turn_aborted` 可以重试；仍运行、完成过、缺失、损坏、错项目、
错 turn、后续 turn、陈旧 generation 或已被替代/已发布的综合都拒绝。
不使用 PID 消失、超时文字或人工声明代替原生终止证据。

成功后写入 immutable receipt sidecar（身份、终止元数据、transcript 摘要，不复制推理
正文），通过 EventWriter 追加同 request 的 generation + 1，保留原失败和上下文，
经既有 dispatcher 执行。claim/channel 锁及原失败绑定保证重复点击与并发请求不会重复
增加 generation；原 generation 的晚到结果仍被原投影规则拒绝。恢复动作不修改 PRD
revision、Owner 签署或已有产物。其他 Provider 暂无受支持回执时，UI 展示明确阻塞原因。

入口：`ChannelMessageActionsMixin._channel_drain_replies` →
`channel_reply_reconcile.reconcile_channel_reply` → 既有回复派发器。

## Owner 候选引用与答复

Critic 的语义拒绝不一定带 `reader_execution_failure`。只要已准入结果有明确 blocking
Owner 问题，就从该次 dispatch 的 contract 和 attempt source manifest 恢复同版候选
refs。必须同时绑定 Run、operation、attempt、fanout、stage、revision、reviewed digest
以及 child/source bindings。缺失或不匹配不搜索旧候选补位，预览读取仍核验产物摘要。

检查点展示候选正文并标注「候选计划待澄清，尚未通过 Critic」。Owner 在主阅读区看到
问题、填写答复、点击「提交答复并重新规划」。UI 复用 `replan-reject`：拒绝当前候选并
附带澄清，Kernel 在同一 Run 内生成新版本；不批准实施、不新建第二个 invoke。
必须等当前审批 refs 完成绑定才能答复；旧预览不能答复新请求。正文缺失时仍允许查看问题并答复，保留正文不可用提示和禁止
直接批准的约束。正式通过 Critic 的 Plan
继续走原审批路径。不会把可读候选自动声明为 `derived_plan_full`。

## 三个交互问题的实现判断

| 问题 | 当前结论 | 实现方式与验收 |
| --- | --- | --- |
| 1. 回答 Owner 问题必须借用 Reject | 本轮已改善 | 独立问题区与答复按钮，隐藏直接批准候选入口；绑定 refs 后同 Run replan，并验证无 writer/重复 invoke。当前为按编号自由文本，不声称已实现每问题结构化选项。 |
| 2. 长时间等待，难判断是否卡住 | 本轮完成分析，后续产品优化 | 已有 elapsed、phase navigator、running/waiting 和失败详情；下一步在主路径汇总当前阶段、进入时间、最近活动及「无需操作/等待 Owner/失败」。最近 token 活动不能当业务进度或剩余时间承诺。先按真实 stage 数据测 P50/P95，再验证扫描复用或上下文去重收益。 |
| 3. Git 禁令混淆当前预览与未来交付 | 本轮完成分析，保留阶段约束 | 测试需求已明确当前 PRD/Plan 阶段禁止 Git 写操作，未来实施另行批准。生产方法应在需求对账中保留原文、适用阶段与未来待决项，在审批页展示本次允许动作；含糊时由 Owner 答复，不能把永久禁令推断成允许提交。 |

耗时依据是上一轮真实证据：Kanban 全程约 34.2 分钟，Channel 从启动讨论到 Plan
约 50.7 分钟；综合约 14 分钟、Planner 约 13 分钟、Critic 约 8 分钟。
这些观测不能直接证明性能 bug，也不足以制定 SLA。未来若提供快速路线，应由用户明确
选择并展示少了哪些评审步骤；不能静默跳过 Critic、削减 token 或更换模型。

阶段约束的语义解释应进入 requirement reconciliation / PRD 方法，不在 Kernel 中用
关键词猜授权。明确永久禁止 Git 时保留禁止；需要未来交付条件时记录 pending decision，
等进入该阶段再请求具体授权。本轮没有修改全局 Git 权限或新增隐式授权规则。
