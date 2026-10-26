# Dashboard 指标增量与 Monitoring 读路径

> 状态: active
> 授权: 用户批准建议 1–4，并要求参考 Chorus/Multica/Gr8r 优化 Monitoring；继续当前 inbox-workflow worktree。来源: docs/design/186 第 19 节、188 第 8 节。

## 范围与边界

任务状态 SQL groupBy、admitted usage receipts/role/backend/hour rollups、核心指标顺序状态与 cursor 原子推进、按 events/tasks/cost 依赖刷新；Fleet/Pulse 复用标量与原窗口计算。Trace 列表按 SQL cursor 选页，Loop 复用完整 trace/run 归属的标量输入。没有迁移 canonical store，没有降低指标数量或数值精度；OA 仍按相关事件 ref 复用原语义归约。

原 `dashboard-initial-generation-budget` 候选的完整冷首屏和多 Run 压测没有满足，已恢复为 backlogs/defer，避免把本次指标完成误标为所有页面首次秒开。Delivery 首次构建继续记录为该候选的一部分；其已有视图缓存未改写。

## 验收

- 任务状态/费用/25 核心字段/Fleet/Pulse 与原来源对账 -> verify: 固定 Cargo 4,188 条、loshu 12,549 条事件，结构深比较零差异。
- 顺序指标及来源生命周期 -> verify: stuck 后 7 事件窗口跨批、轮转、原子替换、半行、事务失败重放、并发读取、未知费用及晚到归属均有回归。
- SQL 增量 IO -> verify: 稳态各项目 10 次追加只读 10 条/2,000B；无变化读取 0 条/0B。轮转全重建另列，结果不重复累计。
- Monitoring -> verify: Trace 稳定分页/嵌套身份、Loop 最新 Goal/多 Run alias、数据库损坏降级与原结果一致；详情保留既有按需读取。
- 页面与合同 -> verify: 下列后端闭包、共享 collector/费用合同、前端 build/unit、Docker Playwright、sentinel 和 mock E2E。

## 验证证据

目录 `/tmp/zf-metrics-incremental-20261026T1605/`：

- `focused-closure.log`: 183 passed；包含 Web routes、snapshot、read_model、OA、SQLite refs mock E2E。
- `metric-contract-tests.log`: 54 passed；collector、费用精度/防重复/归档/backend、Fleet。
- `final-cost-tests.log`: 最终费用按桶读取修改后 46 passed。
- `final-regressions.log`: 最终新增投影/OA/并发/损坏回归 10 passed。
- `verification-plan.txt` + `verification-receipt.json`: `dev-verify plan/run --base dev --tests tests/test_web_metric_facts.py`。累计分支被 planner 的文件数阈值扩为全套，按 AGENTS.md 保留计划并跳过自动 deterministic/serial 层，改跑上述明确闭包；Web typecheck/unit、sentinel 51、mock flow 10 通过。没有运行全量 pytest、真实 provider E2E 或生产多 Run 压测。
- `frontend-build.log`: build/typecheck/bundle budget 通过。
- `playwright-tests.log`: Docker Chromium 1226 的 Board、Overview、观测、Loop、Trace 12 项通过。
- `real-parity.json`: 各结构零差异；原 core 4.819/8.498s，首次 SQL 5.818/11.043s，来源不变 0.031/0.035s。
- `incremental-bench-steady.log`: 普通追加 p50 70.36/74.27ms、p95 74.81/77.66ms；10 条均各 2KB；CPU 均值约 53/57ms。独立进程 max RSS 38,912 KiB，不代表整个 Web 服务。Cargo 旧日志首次自动轮转的慢样本留在 incremental-bench.log。
- `browser-paired.json`: 8001/8002 首次版本资源组构建；`browser-monitoring.json`: 初次指标事实导入；`browser-restart.json`: 已有 SQLite 重启后的独立浏览器上下文。不能混用冷热口径。

8001 未修改；它的核心 metrics 仍返回历史 NoneType 错误，loshu Overview 的 Pulse 等待超时。因此数值正确性以 canonical 对账为准，不能把基线错误/缺失算成新版本减少指标。8002 返回全部 25 核心字段加 OA，保留 6 张观测概览卡与 15 个 Overview/Pulse 项。

## 待归档

实现 commit/hash、最终浏览器数据和 clean-worktree handoff 在 closeout 时补充；docs/design/backlogs 继续 gitignored，不进入 commit。


## 最终页面复测

| 页面（已有 SQL，8002 重启后） | Cargo | loshu-fms |
| --- | ---: | ---: |
| board | 2.753s | 2.211s |
| project | 1.074s | 1.065s |
| events | 5.942s | 6.021s |
| traces | 8.120s | 9.366s |
| behavior-loop | 1.743s | 3.997s |
| delivery | 12.013s | 21.046s |

Overview Pulse 完整出现时间为 3.345/2.593s；8002 无 pageerror。重启后仍可立即读持久化数据，但当前 runtime/session 来源持续变化时页面会显示 refreshing，不能把生成时刻的数据称为完全实时。
