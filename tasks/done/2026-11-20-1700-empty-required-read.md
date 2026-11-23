# 空字符串的必读声明与交付证据一致

> 状态: done
> 实现提交: `a8ea58a5 fix: admit complete reads of pinned empty values`

来源：8×8真实Planner返工在结构预检通过后，被`required_reads[13]`
拒绝；`previous-plan-candidate-3`的`$.backlog_ref`是冻结空字符串，
16:35已有完整页和Provider观察，默认`min_returned_bytes=1`使新attempt无法完成。

- 经真实派发入口冻结空字符串 -> verify: 新生成policy允许0字节，但仍要求精确身份的完整读取证据。
- 空页未读/错源/错摘要/截断，以及非空选择 -> verify: 不被当作空值已读。
- 明确最小字节规则及旧attempt -> verify: 保留原政策，不追改账本或输入。
- 修复通过聚焦、影响闭包和流程验证 -> verify: 受控重新派发Planner，复用现有产物，原失败保留。

修复只解决机械读取声明；不批准Plan、不降低产品验收、不代写Worker结果。

验证：聚焦63 passed；影响闭包9269 passed、7 skipped；Web typecheck/unit通过；sentinel 53 passed；flow smoke 10 passed。历史真实sealed ledger只读复验：旧策略不满足、新生成策略满足；旧账本未改动。真实恢复待部署后验证，因此仍为active。

真实恢复闭环：2026-11-20 17:08:38新Planner attempt派发，旧attempt由内核取消。新policy min=0、旧policy min=1；71/71必读项已覆盖。17:16:57提交admitted（evt-22a89feb7a39），child完成（evt-19cf8f1c4026），17:17:02独立Critic启动（evt-df61b46930ee）。该证据仅关闭空值策略缺陷，不表示Plan已批准或8×8已通过。
