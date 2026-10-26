# Dashboard 指标恢复与观测读取热点修复

> 状态: active
> 基线: `088409c8`；用户要求继续排查和修复上轮遗留问题，区分前端设计、检索与其他原因。
> 来源: `docs/design/186-inbox-and-task-board-human-centered-design.md` 第 16 节及 Dashboard 摘要候选的实测边界。

## 范围与验收

- 重现两项目 metrics_snapshot 错误 -> verify: 定位实际异常调用栈，修复后核心指标和 OA 指标均返回有效结构；未知计价保留 unknown/null 与覆盖信息，不伪造免费。
- 分解完整 snapshot 超时 -> verify: 独立进程逐投影计时、CPU profile、数据量及必要的浏览器 API 时序，区分文件读取与重复 Python 聚合/正文处理。
- 修复已证实的热点 -> verify: 同输入计数/指标/元数据保真；详情和脱敏完整；新旧项目及缓存失效/并发不串数据。
- 最终当前 worktree 8002 复核 -> verify: Docker Playwright 打开 Board、Overview、受影响观测页；报告冷热范围与残余延迟，保留 8001 原服务。

不改真实项目 canonical state，不用假零或删指标换取性能；`docs/design` 与 backlog 仍忽略、不提交。按实际变更闭包验证，完成后本地提交与归档，未授权 merge/push。

## 复现

证据目录 `/tmp/zf-dashboard-deep-fix-20261026T1302`。两项目直接调用 `_metrics_snapshot_projection` 均报 `orchestrator_agent_metrics._operation_usage -> rate.input`：`resolve_rate` 对未知模型返回 None，调用方仍按已知费率解引用。不是 token 对象缺失；上轮错误字符串不足以确定这一点。

## 实现与数据保真

- 未知模型不再解引用空费率；OA operation/checkpoint/overall 保留 token，并返回 `cost_usd=null`、`known_cost_usd`、`unpriced_sample_count`。显式 provider 0 费用合法，非法费用不当作已知费用。OA 故障不再覆盖核心 MetricsCollector 结果。
- Board 批量选择 fanout runtime，每份清单每次请求只读一次；保留 seq/文件/子项 tie 顺序，复用进度。详情仍有完整报告和脱敏。Fanout 列表跳过未使用的报告处理。
- Web 时间窗口复用已解码事件及 segment 范围，保留归档日期语义、签名校验、malformed 区分、追加和轮转。角色 YAML 使用安全 C loader，回退安全 Python loader。operator 技能目录不触发技能调用历史分析。
- 观测页使用自身摘要与缓存/ETag，仍保留全部核心/OA/fleet/provider 指标、费用、Context、Traces/Runs/Fanouts/Candidates 和审计。完整诊断 snapshot 未删除。前端仅打开对应 tab 才请求 Integration/Repair/Pipeline，明确项目的快照先于 workspace 全局扫描。
- 确定性 Event read model E2E 发现脚本沿用旧 flat status schema、且继承真实 workspace 干扰计时；修复隔离目录及 v2 嵌套状态读取，不放宽 API 延迟阈值。

真实 loshu 38 个活动任务完整 JSON 前后零字段差异（含正文、refs、状态和数字）；独立新进程构建任务 54.155s → 32.499s。两项目每个 25 个核心字段与直接 `MetricsCollector.compute().to_dict()` 相比零缺失、零差异；OA 均无 error，未知计价样本 Cargo 17 / loshu 16，没有把 null 当作免费。8001 本身核心指标异常，故核心指标准确性采用 canonical collector 对账，不能只比较两个错误对象。

## 性能证据及剩余限制

Docker Chromium 1226、1440×1000、新 browser context，无 CPU/网络节流、未清 OS 缓存。8001 未重启；以下是单次/少量样本，不是 p95 或纯冷启动基准。

| 页面 | Cargo 8001 → 8002 | loshu 8001 → 8002 | 校验 |
| --- | --- | --- | --- |
| Board 首卡 | 6.586s → 1.767s | 12.329s → 1.680s | 1 / 40 张卡，零 JS 异常 |
| Overview 首摘要 | 1.511s → 1.251s | 2.495s → 3.266s | 8002 每项目 15 项；8001 loshu Pulse 等待 30s 超时，未算通过 |
| Events 初次指标就绪 | 8002 18.080s | 8002 56.195s | 6 卡正常，但依然慢；这轮测量在最终 workspace 延后补丁之前 |
| Events 最终缓存样本 | 8002 2.029s / 4.806s | 8002 7.135s / 7.085s | 6 卡文字逐项与 8001 相同 |

第一版仅缩小观测快照、尚未延后三个面板时曾测得 Cargo 40.002s / loshu 78.163s，失败样本保留。最终观测响应约 254 KB / 1.26 MB。浏览器仍有 60–346ms long tasks，不能说前端完全无成本。

最终后端在独立新进程、不使用 cProfile/分步超时的 loshu 观测摘要耗时 33.711s：metrics_snapshot 8.891s、fleet_stats 10.156s、agents 6.638s、fanouts 2.321s、runtime 1.233s、mutation_audit 2.568s。仍需不同读路径解码/归约同一历史；这解释了首次构建仍慢，不能宣称冷加载完成优化。完整历史 `_snapshot` 修复前为 121.355s。

本任务关闭已证实异常、重复计算和请求放大；持久化增量摘要、SQLite 热读/游标和冷首屏预算仍在 `backlogs/2026-10-26-1011-dashboard-small-summary-apis.md` / `2026-10-26-1011-sqlite-read-path-incremental.md`，触发条件是大历史项目首次访问仍超预算。没有迁移 canonical state，没有通过删指标或改口径换取速度。

## 验证闭包

均在当前 worktree，Python 使用主仓库 `.venv/bin/python`，`PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1`。

```bash
python -m pytest tests/test_web_projection_hotpaths.py tests/test_web_observability_snapshot.py tests/test_orchestrator_agent_metrics.py tests/test_web_metrics_snapshot.py tests/test_metrics_collector.py tests/test_cost_pricing_b_cost_01.py tests/test_skill_invocation_projection.py tests/test_web_board_summary.py tests/test_snapshot_single_hydration.py tests/test_web_events_derived_views.py tests/test_web_kanban_upgrade.py tests/test_web_server.py tests/test_web_health_summary.py tests/test_web_overview_pulse.py tests/test_task_projection_stale_global_failure.py tests/test_lifecycle_fanout_child_queries.py tests/test_payload_shape_renderer.py tests/test_events_log.py tests/test_event_read_model.py tests/test_event_log_read_all_fold.py -q --no-cov -m 'not host and not real_provider and not perf' -n 4 --dist loadfile
```

314 passed；其后事件窗口模块提取相关 29 passed，最后 Fanout 改动与调用方 48 passed，互有重叠，不累加。

- `npm run build` → typecheck、production bundle、bundle budget 通过；完整 Web unit 在本批通过。最终相关 `node scripts/run-unit-tests.mjs tests/pageLoadPolicy.test.ts tests/pageCompatibility.test.ts tests/projectEventBus.test.ts tests/projectRequestScope.test.ts tests/queryClient.test.ts` 通过。
- Docker `mcp/playwright:latest`、host network、`ZF_WEB_BASE_URL=http://127.0.0.1:8002`、`ZF_E2E_CHROMIUM_EXECUTABLE_PATH=/ms-playwright/chromium-1226/chrome-linux64/chrome`，`npx playwright test tests/board-first-paint.spec.ts --workers=1 --reporter=line` → 6 passed，包含延迟 workspace、乱序响应、跨项目、摘要刷新、六指标卡和按 tab 加载。
- `bash tests/e2e/scripts/run_event_read_model_e2e.sh --skip-unit --skip-real-provider --api-port 8004 --run-root /tmp/zf-event-read-model-e2e-20261026T1400` → report passed；event-index ready、lag 0、11 segments、doctor ok；Events/Timeline/Light/Graph 53/113/113/128ms，各在原预算内。此脚本内 browser/provider tier 跳过，Docker UI 验证单独完成。早期污染及 schema 错误的失败记录保留；模拟完成事件已写入隔离状态，临时目录和服务已清理，报告备份到证据目录。
- `dev-verify plan --base dev` 映射 142 个测试文件，无 unmapped；阈值自动扩为 full pytest，按 AGENTS 实际边界规则改用以上相关闭包。没有改 EventLog 写入、Store、schema 或 workflow runtime，此轮不跑无关 serial/flow-smoke，相关 mock E2E 用 Event read model 替代。
- `dev-verify run --base dev --skip-tier deterministic --skip-tier serial --skip-tier web --skip-tier mock_e2e --tests tests/test_web_projection_hotpaths.py --tests tests/test_web_observability_snapshot.py --receipt /tmp/zf-dashboard-deep-fix-20261026T1302/verification-receipt-final.json` → sentinel 51 passed；Web/backend/E2E 已单独执行。初版结构门禁发现 events.py 超上限，拆出 event_windows.py 后复跑通过；保留 initial receipt。未声称 full pytest 或 real-provider E2E 通过。
- `git diff --check` 通过。

证据：`/tmp/zf-dashboard-deep-fix-20261026T1302` 的 browser-comparison/final/warm JSON、core-metric-parity、tasks-parity、observability-timings、tests/build/sentinel logs、e2e-1400/report.json。早期 SIGALRM profile 被内部异常处理吞掉，不用于时延验收。

设计更新在 gitignored `docs/design/186-inbox-and-task-board-human-centered-design.md` 第 17 节、`188-dashboard-workflow-multi-run-sqlite-review.md` 第 6.1 节；不提交这些设计文档与 proposed backlog。仅本地 commit/archive，8002 保留供复看，未 merge/push。
