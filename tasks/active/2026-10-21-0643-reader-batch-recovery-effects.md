> 状态: active

# P1 现场补充：Reader 恢复归属与 batch resume 效果

来源：设计 183、用户授权恢复闭包。现场 evt-16962098c9f9 是有效 abstained；随后 workflow-batch-resume 将 reader context 故障扩为 13 项业务返工，仅凭 task_map.ready / workflow.resume.applied 结算 case（evt-6ced63b70d5e），实际验证环境未修复。

1. Post-Verify reader 即使携带 task_map/candidate refs，也归 reader stage 的有界恢复 -> verify: projection 与 Run Manager caller 不生成 repair_failed_children；candidate.ready 的既有 runtime-only 重试与 writer 业务返工保留。
2. batch resume 将请求收据与新 writer admission 分离 -> verify: 仅 task_map.ready、Plan 待批和同 Run 无关 fanout 不关闭效果；由本请求触发的新 fanout/generation admission 才结算本次“恢复派发”动作，不能解释为产品已验收。
3. 现场错误的 13 任务计划不执行 -> verify: 记录拒绝及原证据；部署后只读回放不再产生同类 batch checkpoint，不直接改 canonical 状态。

TDD：先复现上述两类 RED，再最小修复；影响闭包包含 workflow resume、Run Manager、recovery case、controlled action 与 mock E2E。

## 验证进展

RED：2 failed，日志 `/tmp/zf-reader-batch-effects-red.log`。GREEN：11 文件影响闭包 260 passed；进一步参数化验收共 4 passed，验证 case verifying/resolved/open。覆盖真实 run_manager_tick → ControlledActionService → workflow resume 与 effects 结算调用。只读回放 30252 条现场事件，目标 reader 的错误 batch checkpoint 为 0。
错误 Plan evt-fb91b4593b2e 已拒绝；Run 为 blocked，暂停请求被确定性状态门拒绝，未更改状态。新增 helper 为机械 effect 绑定，保留语义判断在 reader/RM 报告。

`dev-verify plan --base dev --tests tests/test_reader_batch_recovery_effects.py` 纳入新 helper 的直接 caller 证明。`dev-verify run` 本次以 `--skip-tier deterministic` 执行 serial 10、sentinel 51、flow-smoke 10 均通过；本补充采用上述 260 项影响闭包及 4 项最终回归，未再次重复已有 20 项基线失败的广泛套件。
