# 验证基线与首个 Worker 实际接收

> 状态: done
> 实现提交 `1a7dd5e09 fix: bind worker proofs to the signed source manifest bytes`

将本地 dev 和既有恢复验证分支整合，在新隔离实例核对控制面装载、项目配置、首个 Worker 和所用技能。

## 验收

- 检查正常入口的启动、原生派发及读取事实，再试验缺证、摘要不匹配与旧版本 -> verify: 只对实际收到的那次派发给出可复核的版本与技能基线，保留原 Run 成绩。

依据为 8×8 原 Run 的七票保留、技术恢复和最终交付复盘，以及 [199 恢复职责](../../docs/design/199-worker-failure-recovery-and-role-handoff.md)。

## 验证结果

整合本地 dev `bfea5a430` 后，对全新 Run `workflow-47ea0c5201016db7` 验证首发。控制面、WRC、原始及规范化摘要、角色、简报和 14 项技能匹配；原生 `product-scan` 认证读取输入 1,229 字节。派发 `evt-ac0a3b9bf9c6`，读取 `evt-3f988dec2ec3`。此验收在首个读取后停止，后续各阶段的独立通过仍按其各自回执核对。
