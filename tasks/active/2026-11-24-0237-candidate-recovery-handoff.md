# 冻结候选的 OA 恢复与独立补证

> 状态: active

依据设计 199、212、213，以及 G 轮候选 evt-42139d3ccdd7 和 OA 对聚合故障 evt-e6a8a262382d 的六项分诊。

1. 逐项约束派发 -> verify: G 的四条执行未知和两条合同疑问保留原绑定，Writer 零误派，正向产品缺陷仍能选中正确任务。
2. 独立补证 -> verify: 两个证据 Reader 能在相同冻结目标上重验，各自产生独立回执。受保护用例按已有授权补证或单独等待 Owner。保留原始判词及已通过的同组结果。
3. 持久化接力 -> verify: 在请求和结果边界重启，从同一账本恢复，失联或未解决的回执返回负责角色；相同输入的外部效果执行一次。
4. 对原输入重放并进行真实资格验证 -> verify: 单独记录历史输入、mock、真实 Provider 与零干预完整交付各自的结果与环境。

## 验证记录

- `64eca3ac fix: route mixed candidate failures to scoped recovery and reader evidence review` 实现逐项分诊和独立补证。G 轮同一份 28,675 条事件前缀中，旧版本选中 `IMPLEMENT-SUM`；新版本保留六条意见，未选择任何 Writer 任务。冻结的原 Run 缺少新补证授权，两项请求都明确返回 OA。
- PRD 单项探针以可控的上游案例启动，真实 Luna 产生受控回执 `evt-5d33ccf85939`，RM 将尚有合同义务的结果交给 OA（`evt-75559af6a692`）。该探针没有交付最终业务结果。
- `837f70a5 test: pin candidate reader reassessment wake event` 更新了已登记的唤醒事件快照。
- 先导 Run `workflow-c87649819cbe0bb7` 出现工作目录可见、相邻简报不可见。RM 发出关注，暂停前已接纳的扫描结果为零。`8e4be237 fix: verify bound input visibility before starting restricted workers` 从独立 worker 目录实际读取配置 state_dir 中的随机标记。受限 Codex 在原布局拒绝，正常布局通过。
- 后继 Run `workflow-7dbaae8a3501596d` 固定在 `64eca3ac`。两位扫描员与汇总由运行时接纳，`evt-919341bf89d7` 完成扫描。等候本轮真实并发、16 项独立验收、Candidate、Docker、Judge、Goal 与本地交付记录后再判断完整资格。
- `0adeffac fix: preserve candidate obligations across related frozen targets` 修复晚到事实改写旧冻结、受保护义务在后继冻结丢失。真实 G 的历史重试、晚到计划及六条意见前后均重建出历史 Reader 的合同摘要 `7f3aad25…556b7aa`；原 G 缺少不同的原生后继冻结，后继继承由正式 Candidate Verify 入口回归验证。
