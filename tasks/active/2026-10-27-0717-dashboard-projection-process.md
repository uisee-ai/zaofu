# Dashboard 投影进程、刷新与性能验收

> 状态: active

来源：本轮用户明确批准“新 worktree 生成 md、生成 backlog 实施、完成后评估性能”。
设计：`docs/design/191-dashboard-projection-process-and-refresh.md`（用户要求忽略，不提交）。
基线：`f7f0bb85`；分支：`wip/codex-20261027-dashboard-process`。

## 批次与验收

1. 复用已有 Workflow 优化 -> verify: 条件请求、历史图、artifact/config/事件变更、刷新合并与旧数据保留测试通过；不重复改变状态机。
2. 消除重复的配置序列化/源检查 -> verify: 实际热路径耗时对比；内存配置、文件替换、非事件 sidecar 和事件轮转仍使正确范围失效。
3. 接入有界单进程读投影执行 -> verify: Web caller 真实触发子进程；长构建时健康/热读可响应；同资源合并；错误保留旧数据；进程退出/重启/关闭可回收；新数据最终可见。
4. 拒绝不稳定来源的新 generation；Graph 状态与布局分开 -> verify: 构建时来源变化不发布为 ready；结构未变不重新布局，筛选/拓扑改变正确重新布局。
5. 完成 API/浏览器评估 -> verify: 固定数据比较指标字段/精度；记录冷/热/更新/重启结果、长任务及剩余瓶颈；Docker Playwright 通过；清理本批进程。

## 执行约束

复用 SQLite 和现有全量指标算法，不增加 HTTP workers，不建立 durable scheduler，不启动 provider。
只在依赖闭包可验证时收窄失效范围。未证明的优化写明后续触发条件，不降低指标准确性。
设计文档留本地；实现、测试与本任务精确路径提交，随后记录实现 hash 归档；不 push、不 merge dev。

## 验证记录

### 已实现

1. 复用既有 Workflow Graph SQLite/ETag、事件合并刷新、隐藏暂停与历史游标实现；保留当前 dev 的 Review 交互。
2. 配置指纹改为保持相同 JSON 表示的浅层编码，同次读取复用 scope/context/events；冷 Graph 依赖发现不重复解码同一 ledger，已有事件输入不重复建立 operation 索引。
3. Web lifespan 接线一个 spawn 投影进程。Board、observability summary、pulse、Agents 采用显式任务描述符；队列容量 16、资源合并、超时回收、失败重试、停止清理；SQLite 返回完整结果，IPC 不传大 payload。
4. 来源变化拒绝发布混合 generation，失败保留旧值，首次无数据 202；Agents 保留全部指标与精度并显示刷新状态/时间。Graph 仅拓扑改变时重新布局。
5. 固定 Cargo/Loshu 来源比较 API、浏览器、有限 burst 与重启。真实项目只读，Kernel/provider 未运行；8001 属于其他会话，改用固定版本 8041/8042，避免把移动中的 dev 充当基准。

### 验证层级

证据根目录：`/tmp/zf-dashboard-process-20261027/`。本批没有运行全仓 pytest、host 或真实 provider 测试。

- `python scripts/dev-verify.py plan --base dev --tests tests/test_projection_worker.py --tests tests/test_web_operation_snapshot.py --json` -> 无规划错误，最终选择 97 个测试文件。
- `python /tmp/zf-dashboard-process-20261027/run_selected.py` -> 上一轮完整影响闭包 96 个文件、**1185 passed**。
  完整 pytest 参数保存在 `focused-closure-command.json`；使用 `-q --no-cov -m 'not serial and not perf and not host and not real_provider' -n 4 --dist loadfile`。
  planner 在文件数量超过阈值后建议全仓；遵循 AGENTS 不因文件数量启动全仓，实际执行其全部 selected tests。
- 后续 operation 快照复用改动：`.venv/bin/python -m pytest tests/test_web_operation_snapshot.py tests/test_workflow_workbench_cache.py tests/test_web_workflow_request_reads.py tests/test_web_workflow_proposals.py tests/test_workflow_preview_review.py tests/test_web_operations_observability.py -q --no-cov -n 3 --dist loadfile` -> **51 passed**。
  最终新增第 97 个文件为 `test_web_operation_snapshot.py`，已包含在该轮。
- 最终进程/操作回归：`.venv/bin/python -m pytest tests/test_projection_worker.py tests/e2e/test_sqlite_event_ref_operation_mock_e2e.py tests/test_workflow_operation_projection.py -q --no-cov` -> **14 passed**。
  覆盖真实 Web caller/子进程、来源变化、队列合并/容量、启动失败、进程死亡重建、停止清理、原算法指标保真。
- `python scripts/dev-verify.py run --base dev --tests tests/test_workflow_workbench_cache.py --tests tests/test_projection_worker.py --skip-tier deterministic --skip-tier serial --receipt /tmp/zf-dashboard-process-20261027/verify-auto-receipt.json` -> 自动前端 typecheck/unit 通过；跳过的后端层由上述影响闭包执行。
- `npm run build`（web/）-> TypeScript/Vite/bundle check 通过，最大 chunk 454543B，小于 500000B。
- `bash scripts/dev-premerge-gate.sh` -> **51 passed**；此处只验证，未 merge。
- `uv run bash tests/e2e/scripts/run_event_read_model_e2e.sh --run-root /tmp/zf-dashboard-event-e2e-20261027T0800 --api-port 8045 --skip-unit --skip-real-provider` -> mock EventLog/SQLite/Web E2E passed。
- Docker `mcp/playwright:latest`，`workflow-graph-refresh.spec.ts` + `agent-monitoring-refresh.spec.ts` -> **6 passed**；
  `workflow-graph-redesign.spec.ts` -> **5 passed**。包含 mock preparation、展示 fixture、刷新合并/失败保留/隐藏暂停/历史冻结，不作为真实 agent 执行证据。
- 在最终源码重新启动的隔离 mock API 上运行 `workflow-preparation.spec.ts` -> **2 passed**：create-task/idea-to-product 新 Request 提问、补充信息、重新准备、预览及提交 Run 的真实受控 API 链路成功；provider 为 mock。
- Docker 实际页面性能探测 `browser_bench.cjs` -> **12/12** 页面展示成功，无 pageerror；仅隔离 workspace onboarding 入口被 stub，图和指标 API 均为真实读取。

### 性能与指标结果

固定基线 `f7f0bb85`，候选代码和产物哈希见 `code-manifest-final.json`。每项目复制独立 canonical 样本，
排除 worktrees/projections；Cargo 1903 个文件/133.8MB，Loshu 6885 个文件/403.8MB。
source hash 见 `snapshot-manifest.json`，镜像、采样脚本、原始 JSON、截图保留在证据目录。
初次容器缺 git/tmux/procps 的失败和一次 URL 错误已排除，最终两侧使用同一完整镜像。

API 热读交替采样 6 次，中位数（ms）：

| API | Cargo 基线 → 候选 | Loshu 基线 → 候选 |
| --- | ---: | ---: |
| Workflow Graph 200 | 7913 → 1127 | 18235 → 2069 |
| Agents | 1571 → 100 | 2010 → 126 |
| Board | 277 → 83 | 304 → 103 |
| Observability summary | 321 → 141 | 459 → 312 |
| Delivery | 27 → 29 | 30 → 29 |
| Loop | 30 → 30 | 39 → 36 |
| Trace | 34 → 35 | 42 → 39 |

Graph 304 中位数约 315/304ms。Delivery/Loop/Trace 没有显著变化。
14 个 API 面逐字段对账：Graph/Delivery/Trace 完全一致；其余差异均可归因于时间、来源身份或新增刷新元信息。
成本、数量、比率等未发现业务指标偏差或字段丢失；时钟型数值不是逐字节相同，所有差异原值保留。

浏览器每项目/页面/版本各一次，后端已预热，新上下文，显示完整已有数据耗时（秒）：

| 页面 | Cargo 基线 → 候选 | Loshu 基线 → 候选 |
| --- | ---: | ---: |
| Workflow | 18.36 → 8.72 | 31.11 → 12.49 |
| Agents | 9.41 → 3.22 | 13.92 → 2.20 |
| Board | 2.27 → 2.00 | 2.61 → 2.42 |

Workflow 长任务累计 Cargo 2693→2130ms、Loshu 3549→3529ms：首图解析/渲染仍重。
这是单次页面样本，不是 p95；展示旧值的时间与最新值就绪时间分别记录。

连续 12 个模拟事件后，Agents 均达到 seq 4200。写入进程退出到最新 ready：基线 3.75s、候选 2.51s；
候选健康接口中位数/最大 11.6/36ms，基线 34.5/1469ms；Agents 读取中位数约 90ms vs 2149ms。
基线出现一次来源变化 503，候选无请求错误，使用明确 refreshing 的完整旧值。候选等待队列最多 1 项。
候选重启到健康 14.04s；随后 Graph 415ms 返回 304，Agents 118ms 返回完整旧投影、标 refreshing。

### 限制与后续触发

冷加载没有完全解决。首次完整 Graph 就绪 Cargo 12.75→17.57s、Loshu 27.86→38.24s，存在回退；
Board 23.62→17.28s、75.84→68.06s，Loshu 仍需约一分钟。这些是清空投影后的顺序首次读取，组间共享索引，
不是每接口独立空库。Agents 202 仅表示构建已排队，不能当作指标已完成。

主要剩余开销：Graph 冷路径仍同步读取全事件、发现并校验外部 artifact、hydrate preview、深层脱敏、序列化大 JSON。
宽范围事件失效仍存在。持续写入可能长期 refreshing；有限 burst 收敛不构成无限持续写入的新鲜度上界。
Agents 10s TTL + 10s 前端校正 + 构建/排队允许有界轮询延迟，已显示生成时间。

下一批若继续要求降低首次等待，优先做 Request/Graph 依赖声明与固定来源读快照、减少重复 hydration、冷 Graph 后台构建；
保留摘要/脱敏/历史语义。若多项目实测排队主导，再评估第二投影进程；HTTP 多 worker 必须先处理会话和 startup recovery 所有权。
本批不引入新数据库、durable scheduler，不调整 canonical Store/EventLog、不放宽审批。

临时 simulation.done 已经通过 EventWriter/fixture shutdown 写入；8041–8045 测试服务已停止，隔离运行态已删除。
保留原始证据和 detached baseline 源码供核对；真实 8001/8002 未操作。新实施 worktree 保留供后续指定会话合并。
