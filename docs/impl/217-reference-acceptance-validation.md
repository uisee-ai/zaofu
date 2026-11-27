# 217 · 需求参考、产品验收与看板的实施验证

实现起点为 `dev@e1f363c4`。本次使用单机车辆档案 CLI 作为受控产品，固定原始条款、缺项的历史计划、参考资料及有缺陷的候选代码。LS-v1 的历史问题用于选择场景，本次结论适用于此夹具与下列 ZaoFu 入口。[规格与 A1–A6](../sepcs/217-reference-guided-product-acceptance.md) · [可重复夹具](../../tests/fixtures/reference_acceptance/source.md)

## 需求如何交给下一阶段

- 面向用户的讨论、PRD、Planner、Verify 和独立复扫继续使用已有 Skill。讨论者声明参考适用的场景、决定和范围。Planner 保留来源、实现责任与独立验收责任。Verifier 回到当前候选的真实入口，核对原始条款，再处理参考系统与承诺范围之间的差异。
- Kanban 或 Channel 在可修改的 PRD Request 上采纳研究后，Runtime 把采纳事件、来源、摘要和 SHA-256 写入该修订的不可变输入清单。Proposal 冻结后追加的研究留给后续修订。工作流启动时从对应修订或已冻结清单还原描述符。语义取舍仍由参与 Agent 作出。[研究采纳与冻结回归](../../tests/test_adopted_research_handoff.py)
- 卡面和详情使用同一批次的 Task、Run、操作、尝试及正式结果。阻塞理由和等待对象可见，心跳更新活跃时间。已知 Run 在归档前可用账本查看。成员尝试能进入对应工作台，正式产物走现有受检下载入口。[投影测试](../../tests/test_board_execution.py) · [浏览器断言](../../web/tests/task-card-execution.spec.ts)

## 实际验收的边界

| 场景 | 运行与证据 |
| --- | --- |
| A1 调研与预览 | 两个生产入口均有受控适配器和 HTTP 预览回归。Kanban 的真实 Provider 与独立 Planner 试点，在 Plan Preview 取得两个 Task 和八条 AC。Channel 的首次正式 PRD 因缺少登记标题被拒绝，受控修复后正式 PRD Preview 和独立 Planner 的输入逐字相同，两轮分别得到 2 Task / 11 AC、2 Task / 4 AC。 |
| A2 原始漏项 | 未获知预期缺陷的独立 Codex 在原始输入和候选 `918f4d01` 实测发现 F-02 与 F-03 没有对应验收，质保入口返回不可用。另一实例修复，第三个实例在新候选 `b854ed13` 实际调用 CLI 30 次。额外的 16 次独立探针复核前后差异。这三次角色调用按顺序由本次试验调度。 |
| A3 漏证与补证 | 保留写入成功的报告，缺少只读拒写证据时准入拒绝。账号未就绪时保留待验及责任；账号补齐后重启 CLI 实测拒写并重新读取原数据。 |
| A4 范围控制 | 真实复扫把竞品到期提醒标记为后续机会，与已批准的车辆需求分开。确定性回归核对待评审决定在当前目标收口后仍能读取。 |
| A5 断点与变更 | 重建 Orchestrator 后重放同一已准入 finding，只有一个绑定同一候选的修复任务。改换候选或验收规格摘要后旧报告遭拒。独立 Provider 历史没有原生 Run checkpoint，恢复入口明确拒绝；另以原始发现报告通过 IssueFlow 正常入口验证原生接力。 |
| A6 任务看板 | Docker Chromium 实测由卡面进入详情，核对两个并行尝试及已接纳产出，按摘要下载正式文件，并打开未归档 Run。浏览器回归还覆盖排队、重试、等待、终止、心跳及旧格式任务。 |

A3–A5 的断言、重启和证据绑定可通过同一文件复跑。真实模型切片只向 Agent 提供原始产品输入。可重复测试输入另明确了 CLI 字段名，供机器断言使用。[复扫、权限与恢复测试](../../tests/test_reference_acceptance_replay.py)

## 原生试点暴露的恢复错误

IssueFlow 从发现报告生成任务 VEH-02，在原 Run 上交给实现和独立 Verify。第一次 Verify 的 16 份必读输入已齐、checkout 命中候选 `48417a74`，但恢复入口错误要求 TaskStore 把独立 Reader 当成 Writer。它返回 `task_binding_changed`，Verifier 保留 `abstained`。本次修复使用当前 Reader 自己的操作、签发输入和候选集成回执检查身份，保留给其他任务或已终止尝试的拒绝行为。[独立 Reader 的正反例](../../tests/test_scoped_task_reader_recovery.py)

Run Manager 随后确实请求了原地重派。旧版本把失败 Verify 自身的 fanout 当成上游，并把普通 Writer 的候选事件填入冻结候选专用字段。消费者拒绝时，投影仍把“已经重新发信”计为已恢复。修复从原 Run 的生产者、同一集成尝试、Task Map、候选提交及失败的 Reader 批次还原交接。旧版本已经被机械拒绝的检查点可以按修复后的身份重新派发。候选或代次有变更时保留拒绝。[候选恢复与跨 Run 拒绝测试](../../tests/test_writer_candidate_replay.py)

## 复跑

在本分支安装项目的 dev、web、controller extras。运行 `python -m pytest tests/test_adopted_research_handoff.py tests/test_reference_acceptance_replay.py tests/test_board_execution.py -q --no-cov`。使用仓库的 Docker Playwright 入口运行 `web/tests/task-card-execution.spec.ts`。现场运行记录、浏览器 trace、冻结候选 Git bundle 和独立探针保存在本工作树的 `reports/217-a1`、`reports/spec217-pilot`、`reports/spec217-native`、`reports/spec217-controlled-replay`、`reports/spec217-board` 和 `reports/spec217-checks` 下。
