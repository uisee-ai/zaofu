# 两入口冻结验证基线

> 状态: active

来源：用户批准执行 206 下一批目标；基线 dev@3ffacda5。

复用 PRD UI runner，记录完整 Skill 摘要，支持两入口共同产品输入；产品验收与流程协议分开。

## 验收

- 两入口 fake 验证 -> verify: 原始运行与独立审计通过
- 新真实运行 -> verify: 同一候选、冻结输入、实际 Skill 送达和 Plan 身份可追溯

## 边界

仅独立 worktree 和 /tmp 隔离项目，不操作真实 8001，不 push。
