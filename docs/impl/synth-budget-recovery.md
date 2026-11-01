# Synth 预算阻塞的受控恢复

`zf goal set --status active --operation <id> --operation-max-usage-samples 0
--reason <owner-authorization>` 可为因 usage_samples 阻塞的 Task Pipeline 或
`fanout_synth` 登记具名预算修订。该命令不直接修改旧请求，未执行恢复前原限制仍有效。
随后使用 `zf workflow resume --checkpoint <id>` 处理对应恢复检查点。

Runtime 在 fanout 当前性、无终态结果检查后，核对 kernel redrive、此前预算失败
因果链、Owner 修订、operation 请求摘要和原 dispatch 身份，再发送原 briefing。
恢复提示携带当前 Runtime CLI，避免固定版本升级后继续调用旧 CLI。
仍使用原 request/attempt/必读合同，累计费用和开始时间不重置。
`workflow.operation.started` 按 attempt、dispatch、受控 redrive_count 去重。
相同恢复不会重复启动，非预算拒绝、失效修订或已过时 fanout 不适用。

验证：`tests/test_synth_budget_recovery.py` 经 CLI、恢复入口、实际 synth Runtime
调用者及 mock transport 覆盖成功、范围、拒绝、失效、身份和幂等性。
实施记录：[任务](../../tasks/active/2026-11-01-0042-critic-budget-recovery.md)。
