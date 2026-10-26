# Board 独立首屏、正文按需与指标对账

> 状态: done
> 实现提交: `a595c73b fix: accelerate board and overview loading without losing metrics`
> 来源: docs/design/186-inbox-and-task-board-human-centered-design.md 第 11、13–15 节；backlogs/2026-10-26-1011-dashboard-small-summary-apis.md。
> 基线: 09e1a587；用户明确要求优化后用 Playwright 对比 8001/8002，指标精度不变、指标不减少、加载更快。

## 范围

Board 当前项目独立加载，Workspace/health 不阻塞首屏；复用既有状态投影生成 Board 摘要，完整事件正文与 fanout 报告保留在详情接口。保留状态、列计数、费用/token 与其他指标的字段和精度；手动刷新和 SSE 恢复使用相同摘要路径。Overview 的独立聚合优化仍留在原候选，不把首屏优化当作完成持久化增量投影。

## 验收

- 延迟 workspace 与 health -> verify: Board 先展示，同项目数据保持正确，旧项目响应不能覆盖当前页。
- 大报告与重复事件正文 -> verify: Board 不携带正文，详情仍可完整读取；状态、引用及指标值保持一致。
- 8001/8002 同项目 -> verify: 对账字段/计数/金额/token，区分动态来源变化；记录首卡、API 时序、字节与多个样本分布。
- 刷新/切换/SSE 恢复 -> verify: 保留项目身份与事件游标，不用假零换取快速首屏。

## 验证

前端 build/typecheck、受影响模型测试与 Docker Playwright；后端摘要合同与路由调用测试。只重启本会话的 8002，不修改 8001 服务。

## 已实现边界

- 前端按页面统一选择快照；已知项目 Board 的 workspace 列表放在任务响应后读取，health 独立完成；晚到的 bootstrap 不能恢复旧快照；手动刷新清理对应项目读缓存，SSE 断档恢复也取 Board 摘要。
- Overview 也复用相同摘要中的任务、费用、feature 与 runtime 字段，独立 cost / health / overview-pulse 的指标口径不变；不再为 Overview 下载任务报告。此项只改变 transport，独立聚合／增量读模型仍待后续。
- `/snapshot/board` 复用现有分片构建与缓存机制，使用独立 board 缓存键及 ETag；指标定义不变。`_kanban(include_detail_bodies=False)` 在序列化／脱敏前排除 latest event 正文与当前 fanout child 报告；响应适配器也清除归档行正文。完整 light／task／fanout 详情保持原来的正文和脱敏。
- fanout progress 只归约 child status；不为计数投影所有报告。多键引用提取只遍历实际命中键，保持直接键遮蔽、DFS 首个非空引用、零／布尔／小数的语义。启动预热改为 Board 摘要，避免预热大报告和未使用的 task index。
- 没有变更 canonical stores、事件写入、指标计算口径，也没有加入数据库或持久化增量 Board 投影。首轮 61.492 秒样本暴露出冷构建问题，保留在证据中，不能只报告热缓存收益。

## 最终验证与实测

| 项目 | 8001 首卡样本 ms | 8002 首卡样本 ms | 中位数改善 | 解压后响应体 |
| --- | --- | --- | --- | --- |
| cargo-b19f6e06 | [5019, 4459, 4168] | [1557, 1505, 1550] | 4.459s → 1.550s，减少 65.2% | 989,783 B → 20,069 B |
| loshu-fms-0da07163 | [12404, 5226, 12156] | [1556, 2286, 2740] | 12.156s → 2.286s，减少 81.2% | 1,929,908 B → 177,649 B |

12 个新 browser context 样本，服务缓存未清空，不能当作冷启动或生产 p95。Board 测量之后仅切换 Overview 的 transport；其最终构建用真实 8001/8002 页面复核，每端每项目 15 个展示项。项目分别 1 / 40 张卡；任务/归档深度对账一致。费用 63 / 96、健康 7 / 9、Pulse 38 / 41 个数值字段均未缺失；除时钟派生年龄外无数值差异。

- Backend：`PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src /home/min/workspace/public/zaofu/.venv/bin/python -m pytest tests/test_web_board_summary.py tests/test_snapshot_single_hydration.py tests/test_web_events_derived_views.py tests/test_web_kanban_upgrade.py tests/test_web_server.py tests/test_web_metrics_snapshot.py tests/test_web_health_summary.py tests/test_web_overview_pulse.py tests/test_task_projection_stale_global_failure.py tests/test_lifecycle_fanout_child_queries.py tests/test_payload_shape_renderer.py -q --no-cov -m 'not host and not real_provider and not perf' -n 4 --dist loadfile` → 211 passed；此前补充路由域 179 passed，与此有重叠，不累计。
- UI：`npm run build`（含 typecheck/bundle budget）通过；`node scripts/run-unit-tests.mjs tests/pageLoadPolicy.test.ts tests/pageCompatibility.test.ts tests/projectEventBus.test.ts tests/projectRequestScope.test.ts tests/queryClient.test.ts` 通过。dev-verify Web tier 的完整 unit/typecheck 也通过。
- Browser：Docker `mcp/playwright:latest`，`ZF_E2E_CHROMIUM_EXECUTABLE_PATH=/ms-playwright/chromium-1226/chrome-linux64/chrome`，`ZF_WEB_BASE_URL=http://127.0.0.1:8004`，`npx playwright test tests/board-first-paint.spec.ts --workers=1` → 5 passed。8004 已停止，8002 保留。
- `bash scripts/dev-premerge-gate.sh` → 51 passed；`git diff --check` → pass。
- `dev-verify plan --base dev` 映射 136 文件，无 unknown/error；文件数阈值扩展全 pytest 与 AGENTS 的按边界规则冲突，因此采用明确 backend 闭包，run 仅执行 Web tiers（显式 skip deterministic/serial）。没有声称完整 pytest 通过；前批基线失败见既有 done 任务。

仍有边界：新进程冷构建诊断 12.309 秒，不代表 UI 冷加载验收；完整 Observability loshu-fms 两端均 180 秒超时，未完成该全量接口运行时对账。Cargo fleet_stats 的 10 个数值字段完成对账，差异仅 oldest age；两端 metrics_snapshot 都返回同一 `NoneType` 缺少 `input` 错误，因此核心 12 指标准确性未通过验收，不能把错误对象相同写成指标准确。完整冷重建、全量观测和增量 SQLite 仍留原候选，本任务只关闭 Board/Overview 摘要与指标保真范围。

原始证据及可复跑脚本：`/tmp/zf-board-performance-20261026T1151`；设计记录：`docs/design/186-inbox-and-task-board-human-centered-design.md` 第 16 节（gitignored）。
