# SQLite 热读、字节游标与 WAL 维护

> 状态: done
> 来源: docs/design/186-inbox-and-task-board-human-centered-design.md；docs/design/188-dashboard-workflow-multi-run-sqlite-review.md（188 第 5–6 节；186 第 14.3 节）。
> 基线: dev c769680b；worktree zaofu-inbox-workflow。

## 范围

普通读不执行 schema 写入；Web read model 使用已存在的 segment/byte cursor；checkpoint 从请求与高频追赶迁到维护时机。保留重建与必需 sidecar 的所有权。

## 验收

- query_only 热读 -> verify: 无隐式写入。
- 追加一条、轮转、半行、截断 -> verify: 不全扫旧 segment、无漏重，失效游标可恢复。
- 并发读与追赶 -> verify: 无请求内 TRUNCATE 阻塞，记录 WAL 与锁等待。

## 验证与依赖

在首批列表读取优化验收后，补齐游标迁移与恢复 fixture，按 Event/Store 边界执行 sentinel/mock E2E。


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
