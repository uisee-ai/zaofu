> 状态: active

# Reader 恢复投影重复全账本扫描

授权来自本轮持续监工、修复阻塞并记录；只处理现场已复现的Reader投影热点。基线dev b7b5a093。158708事件的只读采样：Supervisor约62.94秒（含cProfile开销），Reader恢复约29.53秒，41次plan_reader_stage_replan约27.12秒。完整记录在LS docs/records/2026-11-08-control-plane-cost-followup/。不能把此热点优化表述为整个Watcher延迟已消除。

## 实施

1. 同一次恢复投影建立短生命周期事件索引，按原事件位置查询类型、ID和失败因果作用域；全量事件仍可追溯，不删历史、不跨快照缓存。
2. 真实workflow_resume调用复用索引；单次调用兼容。保持原stage/Run/Task/operation、cap、成功重置、gap plan、候选替代和授权代次判定及结果原因。
3. 记录现场回放前后结果、耗时及边界；通过影响验证后合dev并受控重载，保留所有业务worker身份。

## 验收

- 多Task和多Run历史失败、已处理/缺失因果、成功重置 -> verify: 原结果与索引路径一致，不能产生额外replan或错误计数。
- 新事件快照 -> verify: 不复用旧索引；新admission/成功仍立即生效。
- 真实恢复投影入口 -> verify: checkpoint输出回归及相关RM/Reader恢复闭包通过。
- 固定历史事件前缀 -> verify: 输出规范化一致并记录热点耗时下降，保留未覆盖的其他控制面成本。
- 交付 -> verify: dev-verify解释闭包、sentinel、相关mock E2E；精确提交/合并/重载记录。未做全套或真实Provider E2E。

实现说明：[Reader恢复投影事件索引](../docs/impl/reader-replan-projection-index.md)。

当前验证：初始6项新API缺失失败，另1项入口测试配置错误已纠正；最终定向71通过（含199与replan mock），serial10通过。同一158708事件前缀3条checkpoint完全一致，14.12秒→7.60秒。295文件影响闭包4149通过、7跳过，sentinel53通过；未执行全套pytest/真实Provider E2E。
