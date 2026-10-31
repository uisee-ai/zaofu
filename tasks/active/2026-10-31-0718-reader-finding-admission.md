> 状态: active

# Reader findings 提交与投影合同一致性

Owner 已授权监工发现问题后记录、修复并继续。dl 的 Planner 提交 evt-e781c1fcb546 被接纳为 completed，但三个 finding 使用 detail 而没有受支持的 message/summary/description/reason；fanout report 投影因此标记 failed/reject 并丢失展示正文。原文仍在不可变 control_result 中。预检只检查数组，未对齐真实消费者。

- 在隔离 worktree 复现错报成功 -> verify: 原版 typed reader / Plan validate / public submit 均漏检的失败测试。
- Reader 准入及 Plan producer/派发前预检复用现有 fanout finding validator -> verify: 坏字段先返回具体错误，不消耗成功身份，不创造新别名或解释语义。
- 支持既有合法别名与 severity 映射 -> verify: 保留完整原始语义，合法成功结果进入投影后仍通过；修正重试可提交。
- 通过直接调用闭包、sentinel、mock flow 后提交、归档、合并和清理本 worktree；保持 dl 当前 Critic 独立执行，待其结果后受控恢复，不修改已接纳工件。

实现说明：[Reader findings 提交边界](../../docs/impl/reader-finding-admission.md)。

验证：原版新回归 9 failed / 5 passed；修复后的 17 模块直接调用闭包 245 passed（含真实 Critic 派发拒绝、public submit 修正重试与两类 mock E2E）。dev-verify 自动 39 文件闭包 690 passed，sentinel 51 passed，flow smoke 10 passed，Ruff 与 diff check 通过。

真实 dl 原始结果在未改 dev 1748cce9 上 pre-admission issues=[]，下游存在 3 条 message 错误；本修复预检提前返回同样 3 条错误。证据：/tmp/zf-reader-findings-live-baseline-20261031.json、/tmp/zf-reader-findings-live-fixed-20261031.json；自动验证 /tmp/zf-reader-findings-verification-20261031.json；直接闭包 /tmp/zf-reader-findings-closure-20261031.log。未运行 full pytest 或新增真实 provider E2E。
