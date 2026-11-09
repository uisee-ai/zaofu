# V5 验证效率修复与真实复测

关联：[实施任务](../../tasks/done/2026-10-30-0903-v5-verification-efficiency-and-runtime-closeout.md)、[实现说明](../impl/v5-verification-efficiency.md)、[前次预算阻塞](2026-10-30-hello-prd-v5-controlled-assembly.md)。

## 当前结论

**原 Task Verify 单操作阻塞已解决；本轮完整交付未闭环，最终被 Run 总预算阻塞。** 实现提交 `0e0321bd fix: bound V5 verification reads and harden runtime closeout`；真实数据字段兼容提交 `d64c7d80 fix: project provider context window into workflow details`；
尾部计量提交 `9a44ad6f fix: reconcile provider usage before session retirement`。

所有修复位于 `wip/codex-20261030-hello-prd-smoke`，没有合并到 dev 或 push，8001 未修改。

## 真实测试身份

| 项 | 值 |
| --- | --- |
| 临时目录 | `/tmp/zf-v5-fixes-e2e-20261030-0946/` |
| Project | `hello-v5-optimized` / `project-a2f0fcdb` |
| Task | `TASK-626329` |
| Run | `prepare-TASK-626329-ce735a8c17e0ecc5` |
| Proposal digest | `5d4e10163bfe0dfdf6a9b039b20e169bc43284f5b9f869d558444027cff1615f` |
| 配置 checkpoint | `4de3e76` |
| 输入 PRD checkpoint | `85d4683` |
| Controller | `zaofu-prd-v5-production-codex` |
| 浏览器 | Docker `mcp/playwright:latest`，host network，Web 监听 `0.0.0.0:8002` |

通过 Playwright 新建项目、Kanban 输入需求、点击 Create task、预览完整 YAML 并点击 Start Workflow。
唯一预置输入为 `docs/prd/hello-world.md`，没有人工编写业务代码或写入 canonical runtime 状态。
产物限 `hello.py`、`tests/test_hello.py`；默认与 `--name ZaoFu` 输出、退出码、至少两项测试均需证明。
本轮沿用 V5 `plan_approval=false`，没有重测人工 Plan 审批/局部聊天编辑。真实观察到候选验证 8-lane 并发，
但一个产品任务不能证明多任务 Impl 并发，也没有重测休眠后的同 session 恢复。

## 配置与链路观测

- 八 lane、impl/verify 池各 8、WRC blocking 和完整 scan/plan/critic/OA/impl/verify/discovery/judge 保留。
- 实际装配的 operation 限制：600 秒、60 samples、1,500,000 tokens、$8；没有调高 token 预算。
- PRD reader/writer idle 和 stuck 配置均为 600 秒；OA 保持 resident。
- 09:49:20 UTC：浏览器批准配置并提交；runtime 自动启动。
- 09:50:59 / 09:52:16：product-scan / tech-scan 结果接纳，Plan 自动接续。
- 09:54:20：Planner 接纳；09:57:57 Critic 拒绝原生 discovery 可能零测试退出成功的计划。
- 09:59:20：Planner 修订接纳；10:01:18 Critic 复审结果接纳，进入 OA pre_impl。
- 10:01:28 / 10:02:33：两个 Scan reader 按 600 秒空闲策略 suspended，保留 session/workdir。
- 10:04:26：OA pre_impl 接纳；10:04:41 单任务 HELLO-1 分配给 dev-lane-0。
- 10:07:18：Impl 提交接纳，源提交 8c2933fd；10:07:28 独立 verify-lane-0 开始。
- 10:09:03：`result validate --scratch --submit` 实际执行，Task Verify passed 被 Kernel 接纳。
- 10:09:11：冻结候选 `198eb4163f05df19853be0e68866d3d62336a946`。
- 10:10:01–21：候选级 verify-lane-0..7 开始并发验证。
- 10:13:06：Run 累计 10035887 tokens，超过 10000000 总上限；8 个候选验证操作被取消，10:13:26 Run blocked。
- Discovery/Judge 未执行，没有 run.delivery.completed 或 run.goal.completed，未重置预算或伪造最终通过。
- Workflow 顶部 Task 绑定、Raw 数值预算、Kernel blocked 终态均在真实页面断言；页面随执行实时更新。

Kanban 首次回答误读主 checkout 的旧模板并描述为 900 秒；实际受控装配使用注册 ID，
预览与展开配置证明为本 worktree 的 600 秒。尝试通过 Edit 修订说明时，消息进入 pending
confirmation 队列；本轮删除该未执行消息并使用原提案继续，未据此声称已验证提案聊天修订。

## Verify 实测与计量修复

| 项 | 前次 Task Verify | 本次 Task Verify |
| --- | --- | --- |
| 结果 | token 超限，未正式提交 | Kernel 正式接纳 passed |
| 持续时间 | 334 秒时阻塞 | 95.454 秒完成 |
| 必读工件 | 批量失败后逐来源读取 | 3 次有界分页，完整来源收据 |
| Provider 累计 tokens | 1510948 | 453075 |
| Runtime 当时采集 | 1510948 | 241738，存在尾部漏采 |
| 提交 | 预校验后尚未 submit 即阻塞 | validate --submit 同一次 CLI 完成 |

这是两次真实生成计划下的观察，不是受控性能基准，不能保证所有任务同比减少。
本次有 20 个工具调用；Provider 最终记录为 input 450020（其中 cache 399616）、output 3055。
它与 runtime 当时记录相差 211337，不能用 241738 冒充最终实际消耗。

定位到 Task stage 释放和 reader context 切换在停止 Provider 后直接清除/轮换旧 session，
导致周期采集来不及补尾。增补修复在这两个真实入口调用既有幂等对账，再轮换身份；
覆盖 append 成功但 cost 写入失败后的重放和保留旧身份。87 项相关测试、11 项 mock E2E 通过。
**该增补没有再启动一轮真实 Provider**，不回写本次 Run 的旧预算 verdict；真实端到端对账仍须下次运行验证。
原 Provider 对话未保留在公开证据中，清理前提取的最终计数见计量 JSON。

固定 8-lane 候选验证及前序规划返工继续消耗 Run 总预算，是当前剩余阻塞。
后续待批准候选：`backlogs/2026-10-30-1020-v5-candidate-fanout-and-total-budget.md`。
语义验证分工仍应由 Agent/受控计划决定，不能硬编码小任务特判、删验收或自动提高预算。

## 自动化验证

- 模块、直接调用方及共享合同定向回归：384 passed、1 skipped。跳过的是缺少外部技能示例的 sensor。
- 注册控制器：12 passed；最后受影响 CLI/任务/通知调用方：76 passed。
- 预算/Graph：55 passed；真实 Provider 窗口字段兼容后 Graph/Web 调用方：56 passed。
- 六组 deterministic mock E2E：17 passed、6 deselected（真实 provider/host 分层不在本组）。
- Web build / TypeScript / unit tests、`dev-verify run` web tier 通过；premerge sentinel 51 passed。
- Docker Playwright `workflow-execution-graph.spec.ts`：15 passed，包含预算总量/缓存/金额未知与上下文占用分离断言。
- 上述集合有重叠，不累加。未运行全量 pytest/coverage；保守 dev-verify 选择扩大到全量时，采用明确影响闭包，未弱化 selector。
- Ruff、diff whitespace、evaluator skill 三份同步检查通过。

## 清理与边界

旧诊断子 runtime `rmar-0cd86772ce2b` 已在其自身 worktree 经 `zf stop --fast --include-run-manager`
停止；原 PID 111354/112615 已退出，候选代码保留。新清理代码覆盖正常返回、取消、异常与超时；
仅停止配置及 session 所属匹配的 runtime，不能证明进程遭 SIGKILL 时 Python finally 仍会执行。

冻结候选独立导出后，两条 CLI 输出符合要求、退出 0；3 项 unittest 通过，包含少于两项测试及测试失败的反例检查。
这些检查不替代未完成的候选验证/Discovery/Judge。

已经 CLI 记录 simulation.done，停止临时 runtime、Run Manager、浏览器、8002 Web 和 8003 测试 Vite。
确认没有项目执行进程、没有派生诊断子 Run；8001 仍监听且未重启。项目和 workspace 已归档为 0600 私有压缩包，
排除 Provider homes、private credentials 与 .env，核验后删除活动状态、action token 和 Web 日志。
候选两份产品文件的独立检查副本仍在临时证据目录，源码修复 worktree 保留。

## 证据

目录：[v5-verification-efficiency-20261030](v5-verification-efficiency-20261030/)。

- [Run 身份](v5-verification-efficiency-20261030/run-identity.json)、[配置与生命周期](v5-verification-efficiency-20261030/compiled-timeout-lifecycle.json)
- [Task Verify 实测](v5-verification-efficiency-20261030/task-verify-measurement.json)、[Provider 最终计数提取](v5-verification-efficiency-20261030/provider-final-usage-extract.json)
- [筛选 Kernel 事件](v5-verification-efficiency-20261030/selected-runtime-events.json)、[独立产品检查](v5-verification-efficiency-20261030/independent-product-check.json)
- [最终阻塞页面](v5-verification-efficiency-20261030/09-run-budget-blocked.png)、[浏览器断言](v5-verification-efficiency-20261030/browser-assertions.json)
- [清理与私有归档摘要](v5-verification-efficiency-20261030/cleanup.json)
