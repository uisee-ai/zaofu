# SQLite 热读、字节游标与 WAL 维护

> 状态: active
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
