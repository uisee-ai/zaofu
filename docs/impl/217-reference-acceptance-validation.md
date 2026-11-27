# 217 · 需求参考、产品验收与看板的实施验证

实现起点为 `dev@e1f363c4`。本次使用单机车辆档案 CLI 作为受控产品，固定原始条款、缺项的历史计划、参考资料及有缺陷的候选代码。LS-v1 的历史问题用于选择场景，本次结论适用于此夹具与下列 ZaoFu 入口。[规格与 A1–A6](../sepcs/217-reference-guided-product-acceptance.md) · [可重复夹具](../../tests/fixtures/reference_acceptance/source.md)

## 需求如何交给下一阶段

- 面向用户的讨论、PRD、Planner、Verify 和独立复扫继续使用已有 Skill。讨论者声明参考适用的场景、决定和范围。Planner 保留来源、实现责任与独立验收责任。Verifier 回到当前候选的真实入口，核对原始条款，再处理参考系统与承诺范围之间的差异。
- Kanban 或 Channel 在可修改的 PRD Request 上采纳研究后，Runtime 把采纳事件、来源、摘要和 SHA-256 写入该修订的不可变输入清单。Proposal 冻结后追加的研究留给后续修订。工作流启动时从对应修订或已冻结清单还原描述符。语义取舍仍由参与 Agent 作出。[研究采纳与冻结回归](../../tests/test_adopted_research_handoff.py)
- 卡面和详情使用同一批次的 Task、Run、操作、尝试及正式结果。阻塞理由和等待对象可见，心跳更新活跃时间。已知 Run 在归档前可用账本查看。成员尝试能进入对应工作台，正式产物走现有受检下载入口。[投影测试](../../tests/test_board_execution.py) · [浏览器断言](../../web/tests/task-card-execution.spec.ts)

## 实际验收的边界

| 场景 | 运行与证据 |
| --- | --- |
| A1 调研与预览 | 两个生产入口均有受控适配器和 HTTP 预览回归。Kanban 的早期草案和独立 Planner 试点取得两个 Task 和八条 AC。随后从实际建组回执的线程 `217-a1` 发布正式 PRD，带版本、采纳理由、数量更正、审计与未采纳的推送决定，正文 51,568 字节，摘要 `9d922f3e…`。同版独立 Planner 的 Plan Preview HTTP 200，T-01 负责三项更正行为、T-02 负责审计追溯，另有各自交接及独立验收项，共 2 Task / 8 项。手册 4.8 修订 C 和采纳理由在 PRD 正文可读，Plan 通过同版摘要和稳定决定编号回查。Channel 的首次正式 PRD 因缺少登记标题被拒绝，受控修复后正式 PRD Preview 和独立 Planner 的输入逐字相同，两轮分别得到 2 Task / 11 AC、2 Task / 4 AC。 |
| A2 原始漏项 | 未获知预期缺陷的独立 Codex 在原始输入和候选 `918f4d01` 实测发现 F-02 与 F-03 没有对应验收，质保入口返回不可用。另一实例修复，第三个实例在新候选 `b854ed13` 实际调用 CLI 30 次。额外的 16 次独立探针复核前后差异。这三次角色调用按顺序由本次试验调度。 |
| A3 漏证与补证 | 保留写入成功的报告，缺少只读拒写证据时准入拒绝。账号未就绪时保留待验及责任；账号补齐后重启 CLI 实测拒写并重新读取原数据。 |
| A4 范围控制 | 真实复扫把竞品到期提醒标记为后续机会，与已批准的车辆需求分开。确定性回归核对待评审决定在当前目标收口后仍能读取。 |
| A5 断点与变更 | 重建 Orchestrator 后重放同一已准入 finding，只有一个绑定同一候选的修复任务。改换候选或验收规格摘要后旧报告遭拒。独立 Provider 历史没有原生 Run checkpoint，恢复入口明确拒绝；另以原始发现报告通过 IssueFlow 正常入口验证原生接力。 |
| A6 任务看板 | Docker Chromium 实测由卡面进入详情，核对两个并行尝试及已接纳产出，按摘要下载正式文件，并打开未归档 Run。浏览器回归还覆盖排队、重试、等待、终止、心跳及旧格式任务。 |

A3–A5 的断言、重启和证据绑定可通过同一文件复跑。真实模型切片只向 Agent 提供原始产品输入。可重复测试输入另明确了 CLI 字段名，供机器断言使用。[复扫、权限与恢复测试](../../tests/test_reference_acceptance_replay.py)

## 原生试点暴露的恢复错误

IssueFlow 从发现报告生成任务 VEH-02，在原 Run 上交给实现和独立 Verify。第一次 Verify 的 16 份必读输入已齐、checkout 命中候选 `48417a74`，但恢复入口错误要求 TaskStore 把独立 Reader 当成 Writer。它返回 `task_binding_changed`，Verifier 保留 `abstained`。本次修复使用当前 Reader 自己的操作、签发输入和候选集成回执检查身份，保留给其他任务或已终止尝试的拒绝行为。[独立 Reader 的正反例](../../tests/test_scoped_task_reader_recovery.py)

Run Manager 随后确实请求了原地重派。旧版本把失败 Verify 自身的 fanout 当成上游，并把普通 Writer 的候选事件填入冻结候选专用字段。消费者拒绝时，投影仍把“已经重新发信”计为已恢复。修复从原 Run 的生产者、同一集成尝试、Task Map、候选提交及失败的 Reader 批次还原交接。旧版本已经被机械拒绝的检查点可以按修复后的身份重新派发。候选或代次有变更时保留拒绝。[候选恢复与跨 Run 拒绝测试](../../tests/test_writer_candidate_replay.py)

恢复时，CLI 和常驻进程曾经同时派发同一批次。现在有存活且持有项目锁的 watcher 时，它独占新批次消息的派发。离线恢复及历史 gate 继续走各自入口。在实际休眠前再次核对具体实例的持久化投递租约，覆盖刚领取、投递中及已发送的窗口。模拟器中 74 项生命周期和调用方回归通过。受控恢复生成的检查点记录由 Kernel 写明来源，独立 Reader 可以核对首轮手工申请的合法回执。[命令入口回归](../../tests/test_cli_recover_workflow.py)

单独的原生 PRD v5 试点从批准输入出发，任务 VEH-02 的实现和单任务 Verify 分别于 10:19、10:28 UTC 取得正式准入。候选 `b1968993` 在 10:28 自动进入 Candidate Verify，10:36 取得独立验收通过回执 `evt-2cc5d9f2aac2`，随后进入从原始需求出发的产品完整性复扫。该试点使用从零启动的 Run，独立于同一 IssueFlow 断点的验证。IssueFlow 的重派保留原 Run 和候选，但 Provider 没有收到新一轮提示，操作在 900 秒 wall-clock 期限到达后正式阻塞（`evt-35ace17f181f`）。此次真实 IssueFlow 试验没有交付回执。独立解包并校验 683 个文件后，重新得到了原检查点和产生候选的 Writer 事件。最后一次休眠修复的实际覆盖见隔离回归。

原生 PRD 的 Judge 对冻结候选于 10:47 UTC 通过。试验进行中，Owner 要求将非 OA 角色统一调整为 gpt-6.1-sol / high。直接保存当前试点的 `zf.yaml` 让交付目标变成了有未提交修改的工作树，Ship 按门禁阻塞。操作员保留新配置修订，还原本轮已批准的文件，按原 claim 和 delivery operation 重试一次。10:50 取得 `run.delivery.settled` 和 `run.goal.completed`；交付提交 `cbd14c4a` 的产品目录与冻结候选逐树相同。新模型策略在原 Run 结束后单独提交并由新控制面确认。新 Run Manager 的实际首个 turn 使用 `gpt-6.1-sol / high`。PRD 的其余 14 个非 OA 角色处于按需等待。IssueFlow 中 6 个非 OA 工作角色及 Run Manager 的配置也已逐个核对，其中 Fix、Verify、Run Manager 取得实际启动参数。这是有人工介入的真实交付样本。

## 验证覆盖

最终影响范围回归有 11,572 项通过、11 项跳过；另有 53 项合并哨兵和 10 项流程 smoke 通过。修订字段诊断后，重跑的 62 项 PRD 入口及模拟回归和 53 项哨兵也通过。Web 类型检查、单元测试及生产构建通过。浏览器覆盖普通看板的 5 项生产回归、3 项工作台组件测试及 2 条实际 HTTP/SPA 旅程。另在当前分支和原 dev 基线复现了已有窄屏布局问题：390px 视窗中，40 个 operation 的工作流图节点被压缩到 6px，测试要求大于 160px。

需求试点中，手动确认建组和发布，试点驱动把同版 PRD 交给真实只读 Planner 并提交预览。首次误选 Channel 的 `main` 线程时，虽然 HTTP 200，产物遗漏原始产品范围，这次结果已排除。使用建组回执指定的线程才取得上述正式结果。模型、真实回复、来源传递和缺项纠正均分别留存。

## 复跑

在本分支安装项目的 dev、web、controller extras。运行 `python -m pytest tests/test_adopted_research_handoff.py tests/test_reference_acceptance_replay.py tests/test_board_execution.py -q --no-cov`。使用仓库的 Docker Playwright 入口运行 `web/tests/task-card-execution.spec.ts`。现场运行记录、浏览器 trace、冻结候选 Git bundle 和独立探针保存在本工作树的 `reports/217-a1`、`reports/spec217-pilot`、`reports/spec217-native`、`reports/spec217-controlled-replay`、`reports/spec217-board` 和 `reports/spec217-checks` 下。
