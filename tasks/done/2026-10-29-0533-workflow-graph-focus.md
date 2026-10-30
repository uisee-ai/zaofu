> 状态: done
> 实现: d2e42ead feat: focus workflow graph on delivery stages

# Workflow Graph 信息收敛与逐层下钻

基于真实项目 Workflow 图的观察，默认图混合了阶段、Agent、Operation、Attempt 与证据等层级，六个同级视图也增加理解成本。保留底层投影和既有深链接，以 Goal、阶段与需要关注的状态作为默认阅读主线，避免虚构运行指标或关系。

## 验收标准

- [x] 默认图以已确认 Goal 和主要阶段为中心，Stage 状态来自现有投影；Agent、Operation、Attempt、普通事件与工件不默认平铺 -> verify: 浏览器夹具和真实数据快照中默认节点可读，状态与来源一致，未生成的 Goal 明确未知。
- [x] 阶段关系在折叠结构后保持可解释 -> verify: 聚合边仅来自已有路径，标记折叠，并对分支、循环、断边覆盖测试；不能凭视觉补造执行或决策。
- [x] 点击阶段查看角色和任务的已观测摘要，相关详细执行、证据和 Loop 可下钻 -> verify: 部分结构、完整投影、无运行数据、历史和失败等状态均有明确行为，旧节点链接仍可使用。
- [x] 一级导航突出 Graph 和 Timeline，其余视角通过次级入口保留 -> verify: Lifecycle、Task、Trace、Delivery 和已有 deep link 可达，Raw、Review、筛选和回放不回归。
- [x] 只在有可信依据时显示简洁状态指标，缺失显示未知 -> verify: 运行状态、阶段状态、已观测执行及数据覆盖不会误报 Task 验收、Token、成本或 ETA；业务状态更新通过既有只读投影。
- [x] 更新实现文档并验证 -> verify: 前端构建、受影响浏览器场景和项目 dev-verify 影响闭包通过；记录真实数据快照与测试夹具的边界。

## 范围

只修改 Workflow 前端视图、必要的纯投影模型、文档与测试；保留 EventLog、运行状态机、受控动作和其他页面的数据。对未绑定 Run 的准备态和旧版 API 保留可用回退。Timeline 继续使用已观测 Operation 时间，Loop 展示尝试循环。

## 验证记录

- `.venv/bin/python scripts/dev-verify.py run --base dev --tests tests/test_stage_loop_projection.py --tests tests/test_web_measure_loop_routes.py --workers 2 --keep-going` -> 312 项 Python、前端 typecheck 与 unit 通过；覆盖本批及前置 Workflow/Loop 基础提交。
- 首次三 worker 回归中既有 `tests/test_web_server.py::TestApiWebActions::test_operator_terminal_starts_accepts_input_and_stops` 因先收到 ready、后续文案未到达而失败，单项重跑通过，完整两 worker 重跑 312 项通过。
- `npm run build` -> 通过；Docker Playwright 图结构、刷新及 Proposal 夹具 20 passed / 2 skipped；两项跳过的 Raw/审批测试在独立 `workflow-prepare-server.py` 确定性服务补跑 2 passed。
- Docker Playwright 在隔离服务分组验证 `workflow-graph-redesign.spec.ts` 的全部 5 项：视角/窄屏、40 节点短窗口、Raw Agent、请求切换与 Plan 引用均通过；模拟服务正常写入 `simulation.done` 并清理。
- `bash scripts/dev-premerge-gate.sh` -> 50 passed / 1 skipped；真实 datal 只读快照 946 个节点中主图显示 7 个阶段/终态，未执行真实 Agent 或修改远端数据。
