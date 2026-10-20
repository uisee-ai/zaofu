# 182-B08 · 条款分类与反例驱动语义审查

> 状态: active
> 来源: 设计182及本轮用户批准的补充修复；沿用独立worktree，不自动批准产品Plan。

## 范围

- 区分来源事实、决定、行为规格、验收用例；原文复用不能作为行为可验收的充分证据。
- 跨域要求绑定全局决定及各受影响行为，拒绝单域AC替代全局契约；加入Q02同类通用反例。
- Critic同时产出本次修订检查与完整维度审查范围；未审项显式登记，不以旧批准或绿灯推断通过。

## 验收

- 更新canonical与分发技能 → verify: 实际Planner/Critic依赖物化和副本一致测试通过。
- 当前Loshu受控返工 → verify: 逐条分类、关键行为输入/前态/后态、全局接口责任及独立反例判断可复核；不伪造产品结果。

## 当前结果

方法与公共实现已提交 `8ce571d feat: add formatted plan reading and semantic review checks`。
真实候选在 Critic `evt-45bb3c9bcea3` 被拒绝；PC-F1–PC-F5 尚未修完。运行时已耗尽
本 generation 自动重试预算，需受控 `stage-replan-new-generation` 授权，故保持 active。
具体修订及验收见[补充记录](../../docs/records/2026-10-20-0528-plan182-semantic-followup.md)。
