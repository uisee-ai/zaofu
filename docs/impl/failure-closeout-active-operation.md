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
