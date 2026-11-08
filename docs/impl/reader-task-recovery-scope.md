# Task Reader 的失败恢复作用域

关联 [199 恢复设计](../design/199-worker-failure-recovery-and-role-handoff.md)；实现入口为 `Orchestrator._on_reader_stage_failure_replan`，共享判定由 `reader_task_recovery_scope.py` 提供。

## 事故与边界

LS-v1 的 FMS17 局部恢复失败 `evt-07deea6b2c05` 被同 Run 其他任务的 Reader 失败消耗了重试额度。原 `_same_replan_scope` 只区分 Run、阶段代次和 feature，随后 `converge_stage_replan_cap` 将所有耗尽都视为全局计划准入失败，阻断整个 Run 并取消无关 FMS12 Reader。

确定性运行时现在从已准入的 Task Pipeline 失败及其因果关系识别 `(Run, Task, failed operation)`。准入必须指向相同 Run/operation 的原始 impl、verify 或 acceptance_review 失败；沿 `rework_of`、唯一 fanout trigger、source/causation 引用追溯。自由文本、`affected_task_ids` 或单独的任务名称不授予局部作用域；未准入、跨 Run、因果环及无法解析的祖先保持保守行为。

该作用域用于重试计数、成功重置和原请求选择。同一失败链仍受原有上限约束；另一个任务或另一次正式失败 operation 不消耗它的额度，也不能用自己的成功重置它。原 Plan 准入失败没有已准入 Task 失败祖先时，仍沿全局处理路径。

局部链耗尽时保留 `human.escalate`、operator 审核和零自动重试，显式绑定 Task/failed operation；不触发整 Run 终止。运行时在证明局部因果链后写入现有 `blocking_scope=side`，使 RM 保留该人工事项但不将其投影为全局派发门禁；不接受 agent 自称局部范围来降级 Owner 门禁。任务自身仍可能被正常失败路由阻塞，依赖该任务的工作仍受原依赖门禁限制。修复不改变任务成功条件、Owner 问题、预算、准入、合同快照或工作目录。

## 验证

- 新隔离回归先 3 失败、2 通过；修复后 focused 60 通过。覆盖不同 Task/operation、同链有界重试、其他任务成功、缺少准入、跨 Run、循环引用和 fanout 归属。
- 实际 `Orchestrator.run_once` 入口及周期终态收敛验证：局部 cap 产生精确升级，父任务与无关任务继续；既有全局 Plan cap 回归仍通过。
- RM 人工门禁投影追加回归先失败：仅设置 `recoverable=true` 仍默认全局等待；补齐确定性 `side` 范围后，局部审核事项保留且全局阻塞列表为空。
- 对真实事件前缀 153771 条进行只读回放：旧实现 `cap_exhausted`，新实现为 FMS17 原失败链生成 attempt 1。未向真实账本写入该模拟结果。
- 线上事故先由运维审核后执行官方 `zf goal set --status active` 恢复；FMS17 已实际继续。此事实与新代码部署效果分别记录，不能把手动恢复算成自动修复证明。

影响闭包、合并与在线部署结果见 [实施任务](../../tasks/2026-11-08-1533-reader-recovery-scope.md)。
