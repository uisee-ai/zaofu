# Inbox / Workflow 引用编辑 Agent 真实验证

> 后续状态变更：23:56 UTC 的后台 Autoresearch 隔离故障重置了 ty 活动账本，以下 ty 通过证据是故障前的真实观察，旧 Plan 链接目前不可用。参见[故障、隔离修复与新验证项目](2026-10-27-0016-autoresearch-context-isolation.md)。

关联：[187 实施说明](../impl/187-ai-sdlc-graph-workbench.md)、[主阅读区修复](2026-10-26-2329-plan-review-entry.md)。分支 `wip/codex-20261026-workflow-graph-ui`，改动在 8002 生效，未 commit/merge；8001 保持 dev 旧构建。

用户仍找不到入口时，先确认两个端口版本不同，并将段落旁铅笔改为常驻「引用编辑」文字按钮。进一步读取真实事件发现，23:40 UTC 的操作来自 `operator-inbox`：旧 Repair/修改请求调用 `chat-orchestrator`，结果为 `queued_no_runtime`。它没有连接 Workflow 已实现的 headless 引用编辑器，因此只修 Workflow 并不能覆盖这个入口。见 [原入口事件证据](2026-10-26-plan-quote-entry/legacy-inbox-proof.json)；事件不提供浏览器端口，不据此断言用户当时打开的是 8001 或 8002。

Inbox 列表及 Plan 预览顶部现在均提供「引用编辑」。它打开与 Workflow/YAML 共用样式和回复框的 `PlanArtifactChat`，支持文档选段、段落文字按钮、真实 headless 对话、Diff、Apply/Discard。移除旧的排队消息路径及重复大文本框。默认审阅视图不发修改请求，保持完整原文、审批概览和附件阅读。候选或调用进行中锁住审批、拒绝、关闭及返回；Apply 后重读原 Plan 状态并刷新 Inbox，已退回版本不可批准。引用仍绑定不可变文档摘要与区间，没有后端状态机改动。

## 实际验证

1. 在 ty 的 Workflow 中点击新「引用编辑」按钮，真实 Agent 将“用户打开 ty 项目的单页计数器”改为“用户打开 ty 的单页计数器”。候选全文等于这一次替换，点击 Apply，产生 `plan.rejected` 事件 `evt-bec449cae7ac`。见 [候选](2026-10-26-plan-quote-entry/applied-agent-candidate.json)、[Apply](2026-10-26-plan-quote-entry/applied-result.json)。
2. 真实 Planner、独立 Critic 和 Kernel 处理后，新 Plan `evt-fbb3199ea3ba` 进入 pending。浏览器与磁盘双重确认：新版全文与候选完全一致，旧文档未被覆盖，正文摘要 `e34168b9aa74a54f522837e42b492fa380b4bd241363858cfc1d5835a166572f`。没有 Plan 批准或 impl/verify 派发。见 [新版](2026-10-26-plan-quote-entry/applied-revised.json)、[状态证据](2026-10-26-plan-quote-entry/final-state-proof.json)、[真实流程日志](2026-10-26-plan-quote-entry/edit-apply.log)。
3. 在新 Plan 的 **Inbox** 预览打开「引用编辑」，用真实鼠标拖选后点击浮动引用按钮，再验证段落文字按钮。实际发送另一条局部文案修改，真实 Agent 返回正确 Diff；候选引用外正文逐字不变，待应用时 Approve 锁住。手机无横向溢出。该候选随后 Discard，新 Plan 正文不变且继续待人工审批；没有调用旧 `chat-orchestrator`。见 [Inbox 真实结果](2026-10-26-plan-quote-entry/inbox-live-result.json)、[真实候选](2026-10-26-plan-quote-entry/inbox-real-candidate.json)、[Diff 截图](2026-10-26-plan-quote-entry/07-inbox-real-agent-diff.png)。

操作：预览右上角「引用编辑」→ 目标段落旁「引用编辑」→ 输入要求 → Send → Diff → Apply；Apply 请求重新规划，新 Plan 仍需人工批准。最终可用入口见下方新项目验证。

## 回归与清理

- 前端 build/typecheck/bundle check 通过，[build-inbox.log](2026-10-26-plan-quote-entry/build-inbox.log)；自动 Web typecheck 与全模型单测通过，[verification-inbox.log](2026-10-26-plan-quote-entry/verification-inbox.log)。此 UI 阶段未运行后端测试；后续隔离修复另外执行了后端闭包及 gate，见故障记录。没有合并。
- Docker Playwright **11 passed**：Inbox 新编辑入口与 Apply 后状态刷新、审批锁、Owner decision 绑定、无写入阅读、错误/迟到附件、完整长文与表格。Provider 和 Apply 在新 Inbox 回归用例中是显式替身，不作为真实 Provider 证据；真实证据见上述 ty 流程。[inbox-browser.log](2026-10-26-plan-quote-entry/inbox-browser.log)。
- Docker Playwright **3 passed**：YAML 小回复框、Workflow Plan Raw/Apply/replan/新版审批、主阅读区选段/重复段落/Unicode/手机。[workflow-regression.log](2026-10-26-plan-quote-entry/workflow-regression.log)。
- 统一使用 Docker `mcp/playwright:latest --network host`，未安装宿主浏览器。8003 隔离夹具均记录 `simulation.done` 后停止并删除临时状态；[Inbox 清理](2026-10-26-plan-quote-entry/inbox-fixture-cleanup.json)、[Workflow 清理](2026-10-26-plan-quote-entry/workflow-fixture-cleanup.json)。8002 保留；原 ty runtime 后续受后台隔离故障影响已停止。
- 真实等待期间出现一次 workflow-synthesis-consumer guard lock 超时诊断；后续 Planner/Critic 正常完成并产生新版预览，没有修改运行时或忽略失败冒充通过。产品实施及产品验收仍未执行。

## 故障隔离后的最终真实复验

新 `ty-review` 项目的 `workflow-3dc6ff5623188714` 从 UI 启动，Codex 完成 scan、Planner 和独立 Critic。首版因 13 个文件超过上限 12 被机械门禁退回，Planner 将 CSS 合入 HTML，同步更新工件后通过检查及 Critic，未绕过门禁。等待超过首轮浏览器脚本的 30 分钟上限，保留[等待超时日志](2026-10-26-plan-quote-entry/recovery-inbox-wait-timeout.log)后重新连接；它不是 Agent 修改失败，也没有计入通过。

Plan `evt-38c86009183e` 于 2026-10-27 00:46 UTC 进入待审批。Playwright 在真实 Inbox 点击顶部「引用编辑」，再点击正文标题旁同名按钮，实际调用 headless Agent 将标题加上“（审阅稿）”。断言完整候选等于一次局部替换、Diff/Apply 可用、候选期间 Approve 锁住、手机无横向溢出。随后 Discard，不改变待审正文。全程零旧 `chat-orchestrator` 调用、零 Plan 批准、零产品实施/Verify 派发。见[真实通过结果](2026-10-26-plan-quote-entry/recovery-final.json)、[实际候选](2026-10-26-plan-quote-entry/recovery-real-candidate.json)、[Diff 截图](2026-10-26-plan-quote-entry/recovery-agent-diff.png)、[最终磁盘/API 核对](2026-10-26-plan-quote-entry/recovery-current-state.json)。这轮复验没有再次 Apply；真实 Apply/replan 证明是故障前第 1–2 步，不混用两次运行。

最终入口：[ty-review Plan](http://10.0.169.194:8002/?project=ty-review-42cb2551&page=inbox&plan=evt-38c86009183e)。原 ty 历史未恢复。新项目和 runtime 保留供人工审阅，人工审批仍为必需。

截图复核还补齐了 Inbox 文档按钮的主题样式，避免依赖 Workflow 页面的 CSS。最终 build/typecheck/bundle check 与真实页面只读入口检查见 [build-final.log](2026-10-26-plan-quote-entry/build-final.log)、[入口复核](2026-10-26-plan-quote-entry/recovery-entry.log)、[常驻段落按钮](2026-10-26-plan-quote-entry/recovery-paragraph-entry-final.png)。未合并到 dev，8001 仍为旧构建。
