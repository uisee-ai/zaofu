# 第一批：评审证据与局部追溯

> 状态: done；实现提交：`eb9d3acf feat: add evidence-bound on-demand Plan and Channel review`

依据：用户批准两批建议，基于 dev 54e4ecf8；完成后合并 dev，不 push。

- 复现 Channel 结构化 evidence_refs 丢失；保留完整评审 sidecar 及可查询引用，非法输入显式拒绝。 -> verify: 定向行为回归、直接调用方与相关合同检查通过。
- 将当前版本、实际审查范围、未覆盖项和阅读依据纳入现有方法及投影，兼容历史缺证据记录而不伪造通过。 -> verify: 定向行为回归、直接调用方与相关合同检查通过。
- 局部 AC 通过明确的 source index / coverage ledger 映射定位来源，不猜测业务关联；覆盖歧义和跨任务反例。 -> verify: 定向行为回归、直接调用方与相关合同检查通过。

验证层：聚焦后端、Web unit/typecheck/build、Docker 浏览器（如涉及展示）、mock E2E、合并前后 sentinel；真实模型实验不冒充已完成。


实现与验收完成：

- 后端影响闭包：565 passed / 1 skipped（缺外部示例技能）；Proposal 拆分调用方 44 passed，新增策略不一致反例 1 passed。
- Web typecheck/unit/build 通过；Docker 浏览器共 18 个不同用例通过（既有 14 + 新增 4）。
- sentinel 53 passed，mock flow 10 passed；v5 Kanban/Channel 两条 UI 旅程均 passed、audit passed，provider-only fake。
- 历史真实 Critic 回复隔离回放：8 对象完整保存，5 个唯一导航引用；未冒充新真实模型质量/成本实验。
- 自动全量 deterministic/serial 未跑，以聚焦影响闭包替代；未运行全库 pytest。
- 详细记录：reports/demand-critic-20261117/evaluation.md（本地忽略证据）；可复用合同见 docs/impl/206-spec-quality-evaluation.md。
