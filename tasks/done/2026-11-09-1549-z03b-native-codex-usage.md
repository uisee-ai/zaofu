# Z03b 本机 Codex 核验与累计期初基线

> 状态: done

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

## 完成记录

实现：`3ec83ebf feat: inspect local Codex usage and baseline existing sessions`。
合入：`6d6cd9e6 feat: integrate native Codex usage inspection and spend baselines`。

- 三流 mock E2E 10 passed，ruff 与 diff 检查通过；合入后在真实 dev 目录重跑聚焦 119 项和 sentinel 53 项均通过。
- 2026-11-09 16:02 UTC 从 dev 执行 `zf controller codex-usage --limit 2`：匹配当前项目 18 个原生 Session；最近 Session 快照为 48,069,984 tokens，范围是从 2026-10-30 创建以来的累计值，含 cache，不是当天消耗。
- 本机读取未发起模型请求、未读取认证文件、未上传任意原生历史；当前来源 subscription，目录无匹配模型价格，估算/实际账单为 null。
- dev 原有 `docs/design/00-index.md` 和 `sima/zf.yaml` 未提交改动按原始 digest 验证保留。
- 本项验收包含实际合入，因此归档放在合入后；没有以未合入状态提前标 done。父 Z01–Z04 继续保留剩余义务。
