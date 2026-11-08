# 精确来源读取与恢复审计身份一致性

> 状态: active；用户已授权监工发现阻塞后修复。

LS-v1 FMS14 Verify 同一 attempt 的精确来源分页已完整输出，但行记录缺 operation_id，recover 按 operation 过滤造成 historical_missing 误报。正式结果准入通过，未绕过读取门禁。

- 复现真实 CLI 精确读取后 recover 误报 -> verify: 分页与非分页回归先 RED。
- 在授权边界记录 operation 身份；旧行仅凭唯一 canonical attempt/operation 与完整 run/task/dispatch/consumer 匹配进入历史投影 -> verify: 错身份、歧义、缺证据保持 missing。
- 不改 ledger、不把历史当当前记忆、不放宽 --resume 或结果门禁 -> verify: required resume / delivery / result 验证影响闭包及 deterministic mock E2E。
- 部署并记录 -> verify: 已结束真实 attempt 只读回放；后续真实精确读取检验新行。

实现与证据：[实现说明](../docs/impl/exact-read-recovery-identity.md)。15项新增回归原始3 RED；针对性39 PASS；141文件影响闭包1505 PASS；serial10、sentinel53、mock E2E3 PASS。真实旧行只读回放9必读中历史缺失6→0。未运行全套pytest或真实provider E2E。
