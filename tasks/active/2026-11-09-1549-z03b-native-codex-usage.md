# Z03b 本机 Codex 核验与累计期初基线

> 状态: active

来源：用户明确要求将 controller integration 集成后，用本机 Codex 统计 tokens 和消费；前一切片已合入 dev。

1. 增加项目范围的本机只读统计命令 -> verify: CLI 在 standalone 可用，按 cwd/session 限定，输出 fresh/cache/output/reasoning 正确且无正文，价格/账单未知不填零。
2. 贯通原生 Session 起点 -> verify: 实际 reader → build_disk_usage_event → Collector，旧/未知来源先基线，新 Session 首次计数正常入账。
3. 兼容旧持久队列 -> verify: 已存在流不重新建立期初余额，新流缺失计数不会形成假零，服务端能力缺失时拒绝上传。
4. 实际本机验证及集成 -> verify: 读取已有 Codex 原生日志；完成 focused/premerge/mock E2E 后合入 dev，保留原有用户未提交改动。

未包含任意本机历史自动上传、实际账单抓取或新的模型调用；这些不是本次只读核验。

## 已验证实现

- CLI/native reader/reconciliation/usage/housekeeping/持久队列聚焦闭包 119 passed；新增 native 测试 10 项。
- dev-verify plan/run 将共享 reader 引用扩展到 340 个测试文件；按仓库分层规则显式排除自动全量 deterministic，以上述闭包验证直接调用边界；serial 10 passed。
- premerge sentinel 53 passed，CLI 手册 currentness 通过。
- 实际 Keycloak/PostgreSQL/Docker Browser 与 ZaoFu CLI 联验通过；使用合成两次 turn，重放后 240 tokens 不重复。
- 本机已只读核验真实累计 Codex 日志，确认 subscription、input 含 cache、reasoning 包含于 output；本地目录无匹配模型价格，金额未知。

后续按本项第 4 条，在合入 dev 并验证后归档；不提前声称已完成合入。
