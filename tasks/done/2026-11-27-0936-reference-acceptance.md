# 217 · 调研交接、产品实测及任务卡面

> 状态: done · `40f254dc` feat: bind research, candidate verification and task progress to current work

范围见 [217 规格](../../docs/sepcs/217-reference-guided-product-acceptance.md)。已授权在隔离项目中试点。

- 从 Kanban/Channel 完成采纳或复用并读两个 Preview -> verify: 追溯到同一参考版本、Task 和 AC。
- 在固定产品输入上回放漏项、竞品独有能力、补证和恢复 -> verify: 区分 mock 与真实模型，记录候选、判据、交接和人工介入。
- 在默认卡面查看执行、等待及最近有效进展 -> verify: 投影回归及浏览器断言，保留关联 Run 和截图。


A1–A6 的对账、实现及各层真实证据见[实施验证](../../docs/impl/217-reference-acceptance-validation.md)。同版 PRD 和 Plan 双 Preview 已核验。真实盲测发现遗漏后，三个独立实例分别发现、修复、复验。PRD 原生 Run 最终交付，经一次操作员处理现场模型配置导致的 Git 阻塞。IssueFlow 原地复验揭示了在有效租约中途休眠的故障，正式阻塞另列。两者均具有独立运行回执。

后续修复提交为 `d8b457b4` fix: retain active reader leases during worker hibernation、`5146c94a` refactor: isolate role suspension lease admission，以及 `c0ef2994` fix: return actionable PRD acceptance and coverage diagnostics。最终影响范围测试 11,572 项通过、11 项跳过，最后的字段诊断另有 62 项相关回归和 53 项哨兵通过。
