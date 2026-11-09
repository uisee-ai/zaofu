> 状态: done — `70af056b` fix: resume exact synth checkpoints after transport failure

# P1：机械 Synth 失败保留原请求的精确恢复

授权：用户要求更新 dev、重启 DL 并从断点继续；沿用已授权的监工遇阻修复范围。

复现：真实 DL 的 evt-4ab48ef2a767 因额度耗尽形成 Kernel noop 失败，原 operation 仍 running。当前 recover workflow 只建议 cancel_stale_operation；infra replan 预览带回早于本次 Critic 的 candidate refs。

- 精确失败 ID 经 CLI 受控恢复 -> verify: 同 request/attempt/预算、实际 transport 新派发、原失败保留。
- 拒绝不可恢复结果 -> verify: 已准入/语义拒绝/预算阻塞/终止 Run/错误角色/损坏请求测试。
- 并发与崩溃恢复 -> verify: 既有 respawn 锁、两次事件追加间中断重放无重复。
- 部署并继续原会话 -> verify: 实际新 artifact read 收据，模型保持 Astra xhigh。

实现说明：[synth-restart-checkpoint](../../docs/impl/synth-restart-checkpoint.md)。

验证：12 个新增恢复合同测试通过；reader/lifecycle/result/read-ledger/EventLog 调用闭包已验证。`dev-verify plan` 保守选择 286 个文件并展开全量，因此按 AGENTS 的影响闭包规则执行聚焦测试，自动 tier 只跳过全量 deterministic；serial 10、sentinel 53、mock flow 10 均通过。最终具体结果记录于监督 verification receipt。

最终验证：重启闭包 87 passed；新增 12 passed；serial 10、sentinel 53、mock flow 10 passed；合并后新增 12 与 sentinel 53 passed。部署版本 `b015335d`，真实恢复事件 `evt-5218112ca7a4`，原 provider session / request hash / attempt / 历史读取账本保持。2026-11-02 03:43:52 UTC 产生新读取收据，`task_execution_input.json` 断点从 1,466,368 前进到 1,474,560，之后继续增长。监督证据：DL `artifacts/supervision/synth-checkpoint-deployment-2026-11-02.json`。Web Docker Playwright 通过、无页面错误。原 Critic 尚在执行，完成产品审查仍由正常 Critic/Owner 门槛决定。
