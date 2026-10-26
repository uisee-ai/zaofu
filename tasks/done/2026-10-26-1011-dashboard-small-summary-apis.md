# Board 与 Overview 小摘要读取

> 状态: done
> 来源: docs/design/186-inbox-and-task-board-human-centered-design.md；docs/design/188-dashboard-workflow-multi-run-sqlite-review.md（186 第 11.3、11.5、13 节）。
> 基线: dev c769680b；worktree zaofu-inbox-workflow。

## 范围

消除 workspace 发现对已选项目的首屏串行阻塞；Board 不返回完整报告／重复 event payload；Pulse 不在请求中全量解析历史与重复 YAML。

## 验收

- 相同项目 fixture -> verify: 状态一致、摘要无大正文。
- 冷/热与持续追加 -> verify: 记录首卡、接口 p50/p95、CPU、响应字节；不把仅缩小 UI 当成后端优化。

## 验证与依赖

首批 UI 与 Workflow 回归通过后，先固定接口字段契约与历史测量基线，再推进 API 调整。


2026-10-26 已拆出 Board 首屏与正文摘要到 [2026-10-26-1151-board-first-paint-and-metric-parity](../done/2026-10-26-1151-board-first-paint-and-metric-parity.md) 并获用户授权执行；本候选保留 Overview/Pulse 聚合和更完整的增量摘要优化，不能随 Board 子项完成一起标 done。

## 本轮实测新增边界

Board 及 Overview transport 的摘要复用已落实到 `tasks/done/2026-10-26-1151-board-first-paint-and-metric-parity.md`，不重复实施。独立聚合及完全冷重建仍保留 proposed。

- 上轮 Cargo 完整 snapshot 的 `metrics_snapshot.error: NoneType object has no attribute input` 已在 2026-10-26 后续排障中修复：实际原因是未知模型的费率返回 None，被 OA 指标消费方直接解引用，**不是 token 信息缺失**。两真实项目的 25 个核心字段已与 MetricsCollector 独立计算逐项一致，未知费用保留 null/known subtotal/unpriced count。见 `tasks/done/2026-10-26-1302-dashboard-metrics-and-observability-hotpaths.md`（完成后归档到 done）。
- loshu-fms 的完整 Observability snapshot 两端均超过 180 秒；推进该页面时需 profile 各聚合、按需摘要/增量游标和无正文读取，不能把 Board 的 1–3 秒样本当作全站性能验收。
- 冷 Board 单独构建仍约 12 秒（测试并行下的诊断样本），新项目/重启场景需要独立冷验收；持久化投影必须覆盖 canonical 输入指纹、失效、重放、并发和精度语义。

2026-10-26 后续已完成按批读取 fanout、共享日窗口事件、真实观测摘要与面板按需加载，详情见 186 第 17 节。尚未完成持久化热摘要/完全冷首屏预算，不重复提交已修复的未知费率和无关面板预取候选。


2026-10-26 最终补充：观测接口及按 tab 加载已实现，25 个核心字段对账通过，六张观测卡与 8001 相同。独立进程 loshu 观测摘要仍 33.711s，浏览器首次聚合样本 18–56s、缓存 2–7s。保留 proposed 的范围是持久化增量摘要与冷路径预算，不再重复排期已完成的接口/价格异常/面板懒加载。触发条件已由真实大历史项目复现；下一阶段须按原验收覆盖重建、追加、重放、迟到、轮转和多 Run 指标归属。


14:12 UTC 已提交版本重启复核进一步确认冷路径失败：Cargo Board 30.290s / Events 46.352s，loshu Pulse 30s 超时、Events 90s 超时。主机存在其他 UI 测试并发，未量化干扰，不据此宣称独立 p95。验收必须覆盖服务重启及并发，禁止只用热缓存证明完成。见设计 186 第 17.5 节和 `browser-final-head.json`。


## 2026-10-26 用户批准实施

用户确认“复用 sqlite”，沿用当前 worktree c72048ad。复用 configured state_dir/projections/read_model.sqlite，不迁移 canonical stores/ledger。读连接禁止 DDL/DML；事件索引用 byte cursor 追加、失效重建。Dashboard 按资源保存可重建摘要及来源/版本，后台追赶，页面不隐藏尚未生成/陈旧状态。重启已有投影与首次空库分别验收；不删指标、不改变数值精度。


## 本次实施与验收（2026-10-26）

授权范围是复用 SQLite 读投影并验证真实页面；没有迁移 EventLog/TaskStore/必需 sidecar，也没有修改多 Workflow 的准入或计费规则。数据库固定使用 configured state_dir 下的 `projections/read_model.sqlite`。

- query_only 热读 -> verify: `test_read_queries_never_execute_schema_or_checkpoint` 禁止调用写连接/DDL，写入尝试失败；所有读连接主动关闭。
- byte cursor 追加、半行、轮转、替换/截断恢复 -> verify: `tests/test_dashboard_sqlite.py` 与现有 `tests/test_event_read_model.py`；追加一条不会打开历史归档，原子重建失败保留上一代。
- 短写事务与差量写行 -> verify: CPU 解码期间另一连接可写；SQLite trigger 证明未变 cost/任务行不重写；移除高频 TRUNCATE checkpoint。
- Board/observability-summary/Pulse 独立持久化 -> verify: API 真正调用新服务，新 app 从 SQLite 读取；来源范围/配置不同不混用，元数据、游标和资源同事务发布。
- 请求不递归扫描 -> verify: 快速关键文件指纹、完整扫描在后台；task/fanout 非事件变化立即触发，深层 sidecar/外部探针按 60s 到期检查。
- 暂无代/陈旧/构建失败 -> verify: 202 无伪造 tasks；单 worker + 每组 single-flight；旧数据继续展示更新时间，失败不能以空列表/零指标覆盖；Pulse 四类来源失败均覆盖。
- 时间刷新与项目隔离 -> verify: Docker 9 passed，覆盖 202、同 seq 刷新、ready 无事件周期刷新、跨项目迟到响应和前后页切换。ready 30s 检查、refreshing 2s 检查；检查周期不等于后台完成时限。

指标算法复用原实现，**事件索引按追加增量、摘要按组重算并差量写行**。不是所有业务指标都已实现 delta reducer。精度证据 `parity.json`：每项目 25 个核心字段 + 任务/归档 JSON + cost 零差异；OA 无 error，未知计价保留 null/known subtotal/unpriced count。8001 仍有原计价错误，不能以错误对象作数值基准。

### 验证命令与范围

环境：`PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1`，Python `/home/min/workspace/public/zaofu/.venv/bin/python`，cwd 本 worktree。

1. 后端完整相关闭包 417 passed（`backend-tests.log`，含 Web server、Event read model、EventLog fold、projection caller、artifact/channel/workflow/operator/metrics）。最终修订后 `python -m pytest tests/test_dashboard_sqlite.py tests/test_event_read_model.py tests/test_web_metrics_snapshot.py tests/test_web_observability_snapshot.py tests/test_web_board_summary.py tests/test_web_overview_pulse.py -q --no-cov`：65 passed；两轮重叠，不累计为 482。
2. `cd web && npm run build && npm run test:unit`：构建/typecheck/bundle budget、完整 unit 通过。
3. Docker `mcp/playwright:latest`，host network、Chromium 1226：`node node_modules/@playwright/test/cli.js test tests/board-first-paint.spec.ts --workers=1`，9 passed。
4. `python scripts/dev-verify.py plan --base dev --tests tests/test_dashboard_sqlite.py` 已检查：含本分支此前变更，150 个映射测试触发 >80 全量扩展。按 AGENTS 的实际领域闭包规则，没有仅因文件数运行全 pytest；直接模块/调用者/共享契约由上述 417/65 覆盖。
5. `python scripts/dev-verify.py run --base dev --tests tests/test_dashboard_sqlite.py --skip-tier deterministic --skip-tier serial --skip-tier mock_e2e --skip-tier web --receipt /tmp/zf-sqlite-dashboard-20261026T1500/final-verification-receipt.json` 执行 sentinel；其他层分别运行。未运行全 pytest、host/real-provider tier、真实多 Run 压测。
6. `bash tests/e2e/scripts/run_event_read_model_e2e.sh --skip-unit --skip-real-provider --api-port 8004 --run-root /tmp/zf-event-read-model-e2e-20261026T1510`：通过，11 个 segment、lag=0、doctor 无问题、4 API 预算通过；已复制 report，隔离状态用 EventLog 追加 simulation.done 后清理，8004 已释放。

### 性能口径和未覆盖范围

同轮 Docker、新 context、1440×1000、无节流，8001 dev 保持原进程：Cargo Board 8.682→1.968s，Events 23.277→2.444s；loshu Board 6.619→2.444s，Events 47.898→2.965s。两边卡片 1/40 张，Overview 完整时均 15 项、Events 6 项，8002 无 pageerror。loshu 8001 Pulse 等待 30s 超时，8002 4.884s。详细原始值在 `browser-paired.json`。

保留数据库、仅重启8002的首轮样本：Cargo Board/Events 2.517/6.162s，loshu 4.045/3.345s（`browser-sqlite-final.json`）。这些不是删库冷启动，也不是总体 p95。初次错误实现的递归指纹仍使 loshu Events 46.008s，失败证据 `browser-restart.json` 保留；快速检查修复后才重新验收。

首次空库生成仍慢：与回归测试并发的诊断样本 Cargo Board/观测/Pulse 55.5/18.9/2.9s，loshu 164.2/75.4/35.9s（`initialize.json`）。已完成“首次构建一次、后续跨重启复用”，没有证明首次空库也能秒开。原生业务 delta reducer、首次建库预算及持续多项目更新能力保留后续候选，不把 202 HTTP 响应算页面完成。

证据目录 `/tmp/zf-sqlite-dashboard-20261026T1500`；设计 186 第 18 节、188 第 7 节已更新且遵用户指示保持 gitignored。无运行时 DB、日志、设计文档或候选 backlog 入提交。最终实现 hash、sentinel 结果、进程复核在归档时补齐。


## 完成记录

实现：`381b57ac feat: persist dashboard read projections in existing SQLite`。本批完成的是前述 SQLite 读路径与跨重启资源复用范围。首次空库、所有业务逐事件 delta reducer、持续多项目/多 Run 压测保留后续候选 `backlogs/2026-10-26-1530-dashboard-initial-generation-budget.md`；历史 proposed 描述不代表这些更广目标已完成。

最终 sentinel 51 passed，收据 passed；实际相关后端 417 passed、最后修订闭包 65 passed，完整前端 unit/build、Docker 9 passed，mock E2E passed。25 文件 / 1302 变更行，风险按跨 API/SQLite 边界评为 high，已经逐模块审查并覆盖读取者/索引恢复/原子性/浏览器合同；没有改 canonical 写入模块或外部 side effect。

实现版本 8002 进程重启后（先直接打开 Events）：Cargo Events/Board 3.256/3.665s，loshu 5.820/4.673s；Overview 首摘要 1.411/1.284s，完整展示项均 15。任务 1/40、Events 6 卡、核心对象 26 字段无 error，六页无 pageerror。样本波动保留，不只采用最好成绩。8001 仍为 50eb6ba1，未重启。

随后 60 次顺序 API 读取全部 HTTP 200，每端点 n=10、nearest-rank 样本分位数（ms），允许后台更新，不能解释为整体负载 p95 或无陈旧性：

| 接口 | 样本 p50 | 样本 p95 |
| --- | ---: | ---: |
| cargo / snapshot/board | 1243.8 | 2373.6 |
| cargo / snapshot/observability | 1313.1 | 2531.6 |
| cargo / overview-pulse | 1077.8 | 3230.4 |
| loshu / snapshot/board | 333.1 | 1403.5 |
| loshu / snapshot/observability | 1606.0 | 4413.4 |
| loshu / overview-pulse | 263.9 | 478.4 |

采样总 wall 67.28s，8002 进程 CPU 累积 72.27s（含后台线程和其他服务请求），结束 RSS 821.3 MiB，非峰值。Cargo 三组为 ready；loshu 三组为 refreshing，携带上次完整值及更新时间。观测到 Cargo/loshu WAL 为 0/206032 B；没有实际生产每查询锁等待仪表，不能用 HTTP 成功冒充零等待，mock E2E 的锁等待值为 0。

追加来源变化探针证实 loshu 有 progress.md、session.yaml、provider logs 与 watcher pid 的运行中变更（`source-change-probe.log`），不是单纯查询日志自失效；构建中变化标记按事实保留。后台按组重算仍有 CPU 成本，后续候选继续记录。

证据：`browser-implementation-head.json`、`api-samples.json`、`final-verification-receipt.json`、`event-e2e/report.json`，均在上述证据目录。已清理模拟状态/8004/Docker 测试进程，8002 留给用户观察。本次仅本地提交，不 merge/push；设计文档/候选未提交。
