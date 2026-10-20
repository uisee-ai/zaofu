# Run Manager 旁路审批与 Run 终态隔离

2026-10-20 的洛书 8×8 执行中，`failure-closeout-activate` 没有 run/task 身份，
其人类审批包已正确标为 `blocking_scope=side`，但终态发布函数从当前 goal
借用了新 run ID，错误发出 `run.goal.blocked`，继而取消仍在执行的 Planner。
旁路候选激活与产品执行不是同一生命周期。

`run_manager_tick` 仍通过原受控路径请求失败候选激活审批。
`_emit_no_progress_run_terminal` 现在要求 action 显式携带 `workflow_run_id`
或 `run_id`；缺失身份时不选择最新 goal，也不从诊断 precondition 推导终态权限。
显式绑定 run 的无进展熔断仍只影响该 run。事件、审批和旧失败产物保留，
不会为了恢复产品流程自动批准失败候选激活。

回归经过实际 `run_manager_tick` caller：同一 state_dir 包含已取消的旧 run、
正在进行的新 run 和无归属失败收尾候选时，审批继续展示为 side，新 run
保持 active；重复 tick 不重复请求，操作者批准后候选仍能正常激活。
另验证目标 run 的三次无进展恢复可以产生唯一终态，较新的独立 run 保持 active。

已误阻塞的历史 run 不通过修改事件日志恢复。操作者应先部署修复，
再使用 `zf goal set --status active --reason ...` 及正式 workflow 恢复入口
恢复有效阶段；已取消的 attempt 不接受补写成功结果。

对应任务：`tasks/active/2026-10-20-1351-run-manager-side-gate-isolation.md`
（完成后移至 `tasks/done/`）。
