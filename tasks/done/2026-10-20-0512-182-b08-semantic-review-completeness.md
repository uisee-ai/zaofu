# 182-B08 · 条款分类与反例驱动语义审查

> 状态: done；实现 `c199520 feat: strengthen plan execution consistency review`，后续反例补充 `2ea8371 fix: require coherent preconditions for negative test cases`。
> 来源: 设计182及本轮用户批准的补充修复；沿用独立worktree，不自动批准产品Plan。

## 范围

- 区分来源事实、决定、行为规格、验收用例；原文复用不能作为行为可验收的充分证据。
- 跨域要求绑定全局决定及各受影响行为，拒绝单域AC替代全局契约；加入Q02同类通用反例。
- Critic同时产出本次修订检查与完整维度审查范围；未审项显式登记，不以旧批准或绿灯推断通过。

## 验收

- 更新canonical与分发技能 → verify: 实际Planner/Critic依赖物化和副本一致测试通过。
- 当前Loshu受控返工 → verify: 逐条分类、关键行为输入/前态/后态、全局接口责任及独立反例判断可复核；不伪造产品结果。

## 当前结果

前轮 `8ce571d` 的方法经 B12–B17 补齐实际动作责任、状态权限、完整事务、
决定快照和负例前态。正式 Critic `evt-4d9584e71768` 已通过最终候选；
明确核对本轮差异并绑定复用未变正文的前轮完整审查证据。
方法及角色消费验证 20 passed、1 skipped，实际同版规格和选择路径可读。
没有以机械计数、作者自检或规划用例代替产品执行证据。
最终证据见[验收记录](../../docs/records/2026-10-20-0847-taskmap-repair-verification.md#最终正式验收)；
原失败历史保留于[补充记录](../../docs/records/2026-10-20-0528-plan182-semantic-followup.md)。
