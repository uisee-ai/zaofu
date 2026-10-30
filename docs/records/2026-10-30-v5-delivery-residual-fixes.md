# V5 残余问题实施记录

状态：B01–B07 已验证完成，真实 Provider 已完成交付并通过 Kernel Goal 终态门禁。

关联：[实施任务](../../tasks/active/2026-10-30-1518-v5-delivery-residual-fixes.md)、[实现说明](../impl/v5-verification-efficiency.md)。

## 复现与修复

- Discovery 缺少父任务分区、内联预算误脱敏、Codex JSON 包装见证：先复现 3 failed，再修复。
- Create task 来源丢失及跨项目引用：先复现 2 failed，再在受控创建入口绑定及校验。
- Graph 活跃写入：先复现 503/409 的 2 failed，再固定事件前缀和 generation，侧文件及篡改仍拒绝。
- 补充 Planner/Verify 方法：输入去重复、完整语义覆盖、有界批量页读取；未调整 lane 或门禁。
- Graph 配置序列化由每次 fingerprint 重复改为每次请求一次；活动事件追加时仅构建一次，计数测试证明。
- 真实浏览器额外发现：实时刷新丢弃 React Flow measured 尺寸，11 个节点中 9 个一直 hidden；只在尺寸变化时重新显示。保留已测量尺寸，由 ResizeObserver 继续处理后续尺寸变化；补充实时可见性/拖动位置和结构到执行摘要切换回归。原 mock 未复现该生产时序，真实运行截图与 DOM 是复现依据。

## 已执行验证

- 运行时/来源/受控动作/消费证据/脱敏：249 passed（20 个定向文件）。
- 活性、预算、fanout、生命周期、watchdog：197 passed（14 个定向文件）。
- Graph/结构与侧文件一致性/API：33 passed。
- Web typecheck、unit、build 与 bundle budget 通过；哨兵 51 passed，flow smoke 10 passed。
- host + mock_e2e：V5 受控装配/启动 1 passed。
- dev-verify 首次指出 routes 未映射，显式传入 API 测试后计划可生成；选择器宽泛影响 515 文件，
  按仓库规则执行上述实际边界闭包而不启动全 pytest；run 跳过 deterministic/serial，其他自动层通过。

## 真实测试

证据目录：/tmp/zf-v5-residual-20261030-153806/evidence。临时 8002，Docker mcp/playwright:latest，独立项目和 tmux。
浏览器负责新建项目、Kanban Agent、建单、Preview 和提交；不伪造运行结果。
真实交付、尾部用量与清理结果见下方闭环记录。

- 项目 hello-v5-residual / project-23156d81；Task TASK-790BC9；Run prepare-TASK-790BC9-766a6c9b61327c40。
- 15:50:48 UTC 正式接纳，15:53:43 Planner 开始；两个 Scan 已接纳。
- 原始输入原文与用户提交逐字一致，critical context provenance=bound_events。
- 两个 Scan 各 9 条读取收据均 observed_in_transcript；摘要校验无失败。
- Preview 及运行配置确认 timeout_seconds=0、token_budget=0、8 lanes、blocking WRC。

- 16:02:07 UTC，Plan/Critic 已接纳，唯一产品任务 HELLO-001 进入 dev-lane-0；8-lane 是容量，单任务不为并行而人为拆分。
- Planner 5/5、Critic 24/24 收据 observed_in_transcript，原始上下文与 29 个版本 digest 均核对通过。

- 图修复后重新 build/bundle 通过；Docker Playwright 三文件 39 passed。16:08 UTC 真实生产构建 12/12 节点可见，隐藏 0、页面错误 0；见 05/06 截图和 graph-visibility-after-fix.json。

- 16:11 UTC 进一步核对 OA：批量 Promise.allSettled 的 `{i,result:{status,value}}` 包装遗漏 1 条完整见证，新增 2 个 RED 用例后补齐两层受控解包。另 38 条继续 unknown（最终逐条审计：36 条未找到完整输出，2 条仅有本次读取前的同正文输出，不证明本次交付）；不重写本轮已封存收据。后续 CLI 提交读取新版解包代码，runtime 调度没有重启或改状态。

- 16:12 UTC 候选 `acd01eb5a2b4deada3692f2320ef6885750a5197` 冻结；独立导出核对仅 hello.py/tests/test_hello.py，两个 stdout/exit 精确符合要求，发现并实际执行 2 项 unittest。证据 independent-product-check.json。
- 16:13:02–16:13:20 UTC，8 个 candidate Verify operation 全部启动且时间重叠；Task Verify 已先完成。
- 高并发发现 execution 请求被较快 structure 更新持续取消，以及后端新缓存覆盖旧 generation 导致在途读取 409。已分别补浏览器/后端 RED；修复为单个在途执行读取、合并最新 generation，并独立核对在途快照的侧文件/前缀，不靠最新 cache 身份撤销合法读取。浏览器首次启动遇 20 秒宿主负载超时，改用显式 60 秒测试预算重跑复现到目标断言。
- 已完成的 candidate Verify 输入页从前轮每角色 12 页变为本轮 9 页（-25%），每页上限仍 8192；这是两次运行观测，Plan 内容受模型影响，不等同严格受控性能 benchmark。

- 并发刷新修复后：Graph 后端闭包 34 passed（共享宿主 339 秒），Docker Playwright 40 passed（3.1 分钟），Web build/bundle 通过。16:24 UTC 仅重启临时 8002 Web，Provider/runtime 持续运行。

- Discovery 已于 16:32:15 接纳（evt-e416167e2ef4），16:32:23 完成（evt-aaac5e485e27）；Judge 16:32:34 启动。Discovery 114 条收据中 46 条完整见证、68 条未找到完整输出而保留 unknown；全部固定版本 digest 校验通过。见 unknown-witness-audit.json、context-proof.json，不能将 CLI 已读等同于模型理解。

## 最终闭环与边界

- Judge 结果于 16:36:55 UTC 接纳（evt-a4bf280b5843）；Kernel 的 Goal delivery 路径在
  16:37:02 发出 ship.completed（evt-49e86be54a8b）与 run.delivery.settled（evt-103839c2534b）。
  16:37:06 run.goal.completed（evt-21d31f55c013），delivery_phase=ship_delivered；judge.passed 是其兼容投影。
  没有伪造 run.delivery.completed 事件，也不靠 Agent 自称完成判定成功。
- 临时产品 main 最终提交 616ef5032aa674d66861d04f1decec9c21dfa2b5，包含被冻结且验证的
  acd01eb5a2b4deada3692f2320ef6885750a5197；再次独立导出、运行两项测试与精确 stdout/exit 检查通过。
  没有推送产品或修改 ZaoFu dev。见 independent-shipped-check.json。
- 修复后正常运行至 simulation.done：505 条 HTTP 200、11 条 304，239 条执行摘要响应记录，
  与结构 scope 核对不一致为 0；无 409/503。停机后另有 9 条 409，是已变更来源的保护拒绝，完整记录保留。
  最终浏览器显示 Run/结束门禁已完成，节点隐藏 0、页面错误 0、alert 0。
- 72 个固定来源版本 digest 均通过；490 条封存读取收据中 383 条完整 transcript 见证、107 条 unknown。
  unknown 分别来自 OA 39 条与 Discovery 68 条，理由已逐条核验；没有倒改历史收据或放宽成功门禁。
- 完整尾部用量 26,482,244 tokens：非缓存输入 1,593,001，输出 99,867，缓存读取 24,789,376。
  价格状态 unpriced，不解释为零费用或新增计费 tokens。前轮在 Discovery 前已阻塞，
  两轮终点不同，不能把总 token 直接当作性能同比；局部 Verify 输入页 12→9 的观测也受模型 Plan 变化影响。
- 本轮使用自动 Plan 接纳，没有复测人工 Plan 审批/引用编辑或跨阶段同 session 恢复。
  已观测 14 次角色休眠并保留 session；确定性活性/恢复测试通过，不能冒充上述真实恢复场景。
- simulation.done：evt-4d8884c8609e。runtime/Run Manager/tmux/Web/浏览器停止，17 个 worker worktree
  先记录 dirty 状态、保存 Git bundle 和私有归档再清理。归档保留工作区内容与 provider sessions，排除认证文件。
  临时状态、token、含凭据的 Web 日志和 workspace 删除；8002/8003 关闭，8001 PID 72631 未动。
  浏览器 trace、截图、最终产品、Git bundle、runtime-evidence.tar.gz 和 cleanup.json 均保留于证据目录。

## 验证复现入口

运行时测试使用 `PYTHONPATH=src /home/uisee/workspace/zaofu/.venv/bin/python -m pytest ... -q --no-cov`。
初轮 249/197 分别见 runtime-tests.txt、liveness-tests.txt；新增见证边界 88 passed 见 nested-witness-green.txt。
最终 Graph 闭包：tests/test_workflow_structure_projection.py、tests/test_workflow_workbench_cache.py、
tests/test_web_source_cache.py，34 passed。Docker Playwright 三文件 workflow-execution-graph.spec.ts、
workflow-graph-refresh.spec.ts、workflow-graph-structure.spec.ts，40 passed，宿主高负载时显式 --timeout 60000。
Web `npm run build`（含 typecheck/bundle）通过，初轮 Web unit 亦通过。
最终 dev-verify-final.json 保留自动层收据：显式两份 Graph tests；跳过已单独覆盖的 deterministic/serial/web，
哨兵 51 passed、flow smoke 10 passed；host mock 1 passed 的独立记录见 host-mock.txt。没有运行全 pytest。
