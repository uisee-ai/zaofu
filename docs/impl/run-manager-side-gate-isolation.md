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

对应任务：`tasks/done/2026-10-20-1351-run-manager-side-gate-isolation.md`
实施提交：`05e9b83`。

## 2026-10-26 GitHub 合并回归

- Autoresearch 的故障上下文通过现有 `loop_iteration` 入口传递，同时继续从实验配置
  解析 `project.state_dir`，不回退到父运行的指标目录。
- Reader 重试继续使用当前派发绑定的可编辑结果草稿，保留完整合同与身份；
  旧的不可修改模板断言更新为实际草稿路径和身份校验。
- Task Pipeline 在 OA 与任务会话优先项之后，按配置声明的 lane 顺序选择空闲且
  能力匹配的执行者，不再用字母顺序覆盖该顺序。

验证入口：`test_autoresearch_diagnostic_context.py`、`test_reader_retry_contract.py`、
`test_fanout_result_draft.py`、`test_task_pipeline_reconciler.py` 和
`test_task_pipeline_orchestration.py`。
