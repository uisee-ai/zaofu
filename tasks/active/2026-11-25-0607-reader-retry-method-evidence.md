# Reader 重试方法变化与绑定证据

> 状态: active

原冻结单路技术弃权时，保留签发时真实可读入口并比较现行方法，给 RM 可核验的动作条件。

## 验收

- 在同一冻结和权限上构造 unchanged、changed、unknown 和错配 -> verify: 仅有具体改善且授权有效时给出单路恢复依据，状态变化按内核受控动作执行，已成立的人闸继续受保护。

依据为 8×8 原 Run 的七票保留、技术恢复和最终交付复盘，以及 [199 恢复职责](../../docs/design/199-worker-failure-recovery-and-role-handoff.md)。
