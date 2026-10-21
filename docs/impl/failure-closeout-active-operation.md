# 在途规划与失败收尾的边界

失败收尾将诊断候选提升为待执行任务，需要 owner 批准；它不能仅因 worker 五分钟没有
新事件，就把仍在执行的 workflow 判成 blocked。该终态会取消 reader fanout，导致正在
返修的 Planner 被中断。

Run Manager 在构造 failure-closeout-activate 候选前，使用已有
`reduce_workflow_operations` 查看当前 Run 的 operation。存在 `running` operation 时
延后这项附属收尾；不更改其批准策略，不放行 Plan，也不制造 heartbeat 或完成事件。
其他 Run 的 operation 不影响当前 Run。operation settled/failed/blocked/cancelled/
superseded/interrupted 后不再由本规则延后；既有预算、lease、恢复和人工处理机制仍负责
真正停滞的 operation。运行状态长期未结算应由这些机制处理，不能以批准诊断 backlog
代替 operation 生命周期管理。

复现证据来自 cargo-plan-regen-20261021 的 `evt-2d43b5ef532a` 前事件快照：
返修 operation 为 running，但旧实现仍提出1项失败收尾，随后根目标阻塞并取消返修。
修复后同一快照不产生该收尾候选，历史运行态保持不变。

测试 `tests/test_failure_closeout_active_operations.py` 覆盖实际 projection/tick 入口、
长时间静默、各终态后仍需批准以及不同Run隔离；复用 run-manager/router/failure-to-eval
回归和确定性 Flow smoke。此修复不证明计划语义或产品验收通过。

本轮执行与后续真实规划验证见
[任务记录](../../tasks/active/2026-10-21-1300-plan-rework-and-comparison.md)。

## 真实返修暴露的验证时点事实缺口

真实27任务返修得到Critic通过后，operator仍复现一项时点冲突：命令声明
`execution_stage=after_independent_verify`，但owner为`candidate_verify`，当前运行时
仍把它和对应AC选为candidate Verify必需项。该命令又要求本次独立Verify最终报告已准入，
仅有文字后置声明不能消除未来收据依赖。

`plan_execution_review.review_execution`现在复用`verification_commands`中的实际AC和
命令选择函数，为每个任务输出`verification.task_requirements`，并区分声明的阶段注释
与`required_by_verification_owner`。该报告经既有CLI和Plan preflight进入Critic必读输入；
不改变命令执行、owner筛选、批准策略或工作流阶段，不产生语义通过/失败结论。
Planner/Critic按执行一致性技能核对用例前置、收据时点与实际消费者，保留最终义务。
新增CLI行为回归覆盖自定义阶段不覆盖选择、human义务和异任务生产者的区别。
