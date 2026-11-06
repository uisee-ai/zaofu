# 199-01：作用域等待与可执行队列选择

> 状态: active

来源：[199](../../docs/design/199-worker-failure-recovery-and-role-handoff.md)；关联 A04；R04/R10/R11；C02。

授权：2026-11-06 用户要求基于 199 在新 worktree 生成 backlog 并实施。
基线：`cff2a8d1`；范围仅 ZaoFu，真实 Provider/宿主部署验收独立记录。

## 实施

复现无关任务被队首或任务 gate 阻塞；沿共同准入路径选择合法动作，保留真正 run 级 gate。

## 验收

- 任务级 gate、队首 preflight 失败与 run 级暂停 -> verify: 无关合法动作可选，相关动作及真实暂停仍阻断；验证 RM 调用方。
- 修改通过实际 runtime/CLI/briefing 入口生效 -> verify: 调用方回归与影响闭包通过。

## 记录

- 实施：已修复队首饥饿、任务及依赖门禁；agent/operator 执行前复核当前 gate、案卷和前提；未知审批有拒绝回执。
- 对应验证：`tests/test_run_continuation_scope.py tests/test_run_manager_scoped_selection.py tests/test_run_manager_action_admission.py`；最终命令/结果见 [实施记录](../../docs/impl/199-worker-recovery-implementation.md)。
- 本轮关闭的是上述工作包验收，199尚存缺口不计作完成；针对性回归及调用方检查通过；广域验证无新增失败、仍有6项隔离复现的基线失败，详见实施记录；等待实现提交。
