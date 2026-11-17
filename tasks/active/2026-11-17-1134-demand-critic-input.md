# 按需 Critic 输入与返修收敛

> 状态: active

用户已批准按建议生成 Spec 并实施，追加要求真实 Channel → Plan Preview 验证。依据 [208 Spec](../../docs/design/208-demand-critic-input-and-convergence.md)，基于 dev bbefdff0 的隔离 worktree。

- [x] S1 / AC1：none 角色选择、STOP 失败及受控重试 → verify: 消费者、投影、API 闭包回归。
- [x] S2 / AC2–4：无损包装拆分、首次定向输入和既有增量保护 → verify: 真实派发、阅读回执、身份/权限反例和计量对照。
- [x] S3 / AC5：修正场景边界、验收责任及角色阅读方法 → verify: 三份分发一致、负样本与真实输出阅读。
- [x] S4 / AC6–7：固定样本对照和真实 Channel → Plan Preview → verify: 同版 PRD/Plan、策略/用量、Docker 页面/下载证据；不启动产品实施。

实施完成后运行 dev-verify 与 sentinel，记录实际测试层和未覆盖项，提交实现，再以实现 hash 归档。未授权本轮 merge/push。

## 当前验证证据

- S1：真实注册编译器重现 none 与 requested_roles 冲突；STOP 一次终结并保留原因，同一需求修正后重试成功。补齐 none 聚合的当前作者完整度声明；Docker mock none 到待审批完整 Plan。
- S2：首次 targeted、full 无损分区、逐来源回执、显式策略与历史基线保护通过。冻结历史输入的 targeted 字节减少 22.0% / 37.6%，属于选读交付量，不是费用预测。
- 自动层：414 文件影响闭包 5,539 passed / 8 skipped；追加 none 聚合及调用者回归 195 passed（更早含 reader 的闭包 349 passed）；serial 10 passed；Web build/typecheck/unit、sentinel 53 passed、flow-smoke 10 passed。
- 方法层：四个技能包的源和两份分发一致。生产 gpt-6.1-sol / medium 固定样本 full/targeted 各 4 次，干净样本均通过、三类植入缺陷均定位。该校准不替代完整真实产物评估。
- S3/S4：真实 Channel → 确认 PRD → v5 Workflow → 待审批 Plan 已完成；774 行 PRD、139 行 Plan、4 个任务及 34 个场景完成独立阅读，无阻断缺陷；targeted Critic 一次批准，无语义返修。
- 首次真实 UI 失败为 Critic 普通完整度标签覆盖同版作者 full 声明；已按候选合同、版本、正文摘要和唯一作者绑定修复。原失败保留，同一真实 Plan 的 Docker 只读复验及全新 mock 聚合通过，未批准实施。相关 78 文件闭包 1,526 passed / 7 skipped；Channel 类型反馈闭包 542 passed。
- 实测必读字节减少 46.4%，Critic 角色完整输入（含缓存）仍 4,749,694，为 Planner 的 2.30 倍；耗时 18.12 分钟。本批完成不表示真实总成本下降。Channel 提示补丁由回归验证，旧调用自行修复成功不归功于新提示；Planner 路径文本误判作为后续项保留。
- 本地忽略证据入口：`reports/critic-scope-20261117/evaluation.html`（含 Markdown、日志、原始失败和修复后证据）。
