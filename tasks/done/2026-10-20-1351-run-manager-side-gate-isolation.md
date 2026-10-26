> 状态: done

实施提交：`05e9b83 fix: isolate unscoped recovery approvals from run terminals`

# Run Manager 旁路审批不得终止无关执行批次

授权范围：用户已要求自主完成 8 路 Impl/Verify 的重规划与执行；本项修复实际阻塞该执行的机械运行隔离缺陷，不扩大失败候选激活或远程操作权限。

证据：`loshu-impl8-20261020` 的 `evt-20db958577b0` 将新 run 标为 blocked，来自没有 run/task 身份的 `failure-closeout-activate`。该 action 引用 06:24 的 `evt-ad2247f96f8c`，人类审批包自身为 `blocking_scope=side`；新 Planner 于 13:32 已派发，随后被 run terminal 取消。

## 实施与验收

- 重现无归属旁路审批误终止当前 run -> verify：实际 `run_manager_tick` caller 测试先 RED，要求仍发出旁路审批但不产生 `run.goal.blocked`。
- 收紧 run terminal 的身份要求 -> verify：没有显式 run 身份的 action 不借用当前 goal；显式绑定另一 run 的无进展恢复仍只终止该 run。
- 保留审批与候选激活语义 -> verify：重复 tick 不重复审批；批准后原失败候选仍能通过受控路径激活。
- 执行影响闭包与哨兵检查 -> verify：定向 Run Manager、run scope、orchestrator/CLI 相关自动验证及 deterministic mock E2E 通过。
- 更新实现说明 -> verify：[运行隔离说明](../../docs/impl/run-manager-side-gate-isolation.md)记录边界、证据和恢复方式。

## 验证结果

- TDD：实际 `run_manager_tick` 回归先 RED（意外产生 blocked），修复后两项针对性回归 GREEN。
- 定向影响闭包：`tests/test_run_manager.py tests/test_run_scope.py tests/test_run_manager_rework_triage.py tests/test_runtime_self_recovery_closures.py tests/test_run_admission.py tests/test_goal_dossier.py tests/test_goal_dossier_owner_delivery.py tests/test_lifecycle_fanout_child_queries.py`，`-q --no-cov -n 4 --dist loadfile`，316 passed。覆盖终态生产者、run 隔离、重试 caller、终态取消及交付投影消费者。
- `scripts/dev-premerge-gate.sh`：51 passed；`scripts/run-flow-smoke.sh`：10 passed。
- `scripts/dev-verify.py plan --base dev` 保守扩展为 248 个测试文件；本修复仅收紧终态身份检查，按 AGENTS 的单域定向验证规则不运行全量 pytest。已执行 `run --base dev --skip-tier deterministic --skip-tier serial --receipt /tmp/zf-side-gate-verification.json`，其 contract/mock E2E 均通过，定向 pytest 结果如上。未执行 host/real-provider 或全量发布验证。
