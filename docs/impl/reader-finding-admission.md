# Reader findings 提交边界

`workflow-read` 的 typed adapter 和 Plan candidate preflight 使用 fanout 的同一
finding validator。`findings` 必须是数组，条目必须能形成有效正文、严重度及定位字段；
非法条目在提交或 Critic 派发前返回具体诊断，不能先被接纳为成功再在报告投影中失败。

首选 `message` 保存正文；既有 `summary` / `description` / `reason`、结构化
observed/expected 等表达继续由原 validator 处理。未支持的 `detail` 不被静默猜测为正文。
预检不解释 finding 的业务结论，不修改源工件，也不把有高风险发现的成功扫描改判为执行失败。
生产者应修正同一 scratch 后重新验证和提交。已接纳历史工件保持不变，通过新受控代际恢复。

回归测试 `tests/test_reader_finding_admission.py` 覆盖 producer 预检、真实 submit
拒绝及修正重试，以及 canonical report 投影，保留 artifact_production 与语义结论的区分。

## Reader 裁决与 fanout 投票

`workflow-read` 的 `verdict`、`failure_class` 描述 Reader 结果，`recommendation` 供 fanout 投票。依赖阻塞保存为 `blocked / dependency_blocked / abstain`；主动弃权保存为 `abstained / reader_abstained / abstain`。显式弃权并附上依赖阻塞分类时保留该分类。执行故障沿用原有 `reader_execution_failure` 处理，artifact producer 完成执行时可以报告阻塞的评审对象。

提交和历史 fanout 投影识别旧 Reader 投票 `block`、`blocked`、`abstained`、`rejected`，按现行枚举输出。历史事件和按摘要冻结的正文保留原值。当所有失败子项一致地报告同一种不可用结果时，聚合与阶段事件保留该分类；依赖阻塞与真实拒绝并存时保留混合失败。此转换仅适用于 Reader，不改变 Critic、Verify 等其他 profile 的提交约束。

新输入的 `subject_verdict` 由 Reader 适配器计算。一个工件生产者可以报告对象已通过，但其自身的交付执行失败。准入保留这两层结论；live fanout 与重放读取已绑定的结果，只转换旧的投票拼写，保留原始对象裁决。
