# Workflow DSL 自然语言编辑与单次启动确认

关联：[187 实施说明](../impl/187-ai-sdlc-graph-workbench.md)。本次继续使用 `wip/codex-20261026-workflow-graph-ui`，尚未 commit 或合并到 dev。

## 行为

- Raw 同时显示完整脱敏 YAML 和 Agent Chat，无 Edit with Agent、必选字段或讨论/修改模式切换。
- `workflow-dsl-editor.v1` 专用 Prompt 位于 `src/zf/runtime/workflow_dsl_prompt.py`，由真实 `WorkflowPreviewReview.discuss` 调用 headless 时使用。服务端提供当前 YAML、版本绑定与字段白名单；Agent 判断解释、追问或局部修改。
- 单次可生成多个配置字段的 Diff，例如 lanes + model 或 lanes + Plan 审批。引用需求仍只改选中片段；拒绝未知字段、只读字段、重复字段、错误类型及混合需求/配置补丁。Diff 应用后只生成新版 Proposal，未直接应用 `zf.yaml` 或启动 Run。
- Review & Approve 独立入口取消。Raw 的 Start Workflow 是一次显式启动确认，按同一 Proposal 绑定依序调用配置应用和提交；应用失败不提交，已应用配置的重试只提交。待应用 Diff 期间禁止启动、切换请求和关闭面板。
- Details 保留阻塞诊断、需求引用讨论、准备及运行控制。Answer Questions 与后续 Plan Preview 审批继续保留；已有 Run 的快照不可编辑。

## 验证与边界

1. 后端影响闭包：113 passed，含 Preview、受控配置/提案、Web 入口、图投影和 intake semantic handoff mock E2E。随后把完整 YAML 从事件正文移为既有 snapshot 引用，再执行 Preview/Chat/blocked 回归：43 passed。
2. 前端：build/typecheck/bundle check 通过；`dev-verify.py run --base dev --tests tests/test_workflow_dsl_chat.py --tests tests/test_workflow_preview_blocked.py --skip-tier deterministic` 的自动 Web typecheck 和完整模型单测通过。规划器的保守 API 全量调用闭包未执行，后端采用上述显式影响闭包；没有运行全量 pytest。
3. Docker Playwright：8 个用例通过（首轮 7 passed、1 failed；失败是旧布局断言仍查找已移除的审批按钮，更新定位后该用例 1 passed）。覆盖六视图、窗口自适应、直接 Chat、真实受控修改/提交服务、配置应用失败不提交、两个创建入口的问答、引用修改、移动布局和独立 Plan 审批。Provider、runtime 启动和 Plan 投影使用明确的测试替身。
4. 最终 sentinel：50 passed、1 skipped。
5. 真实 Codex：在隔离项目和端口 8004，经 Docker Playwright 调用安装的 Codex headless。三次回复使用同一个 Provider session：解释 lanes 不产生补丁；模型未确定时追问且不产生补丁；明确请求后同时生成 `config.0.lanes: 1 → 2` 与 `config.1.workflow.plan_approval: false → true`。应用并刷新后 revision 3 保留两项变更，canonical 配置仍是 lanes 1，没有提交或启动 Run。该测试仅注入候选阻塞项，Agent 没有 mock；不作为实际任务并行执行证据。调用使用 read_only 逻辑 profile；本机 Provider sandbox 沿用宿主环境的 danger-full-access，不能将此测试表述为 OS 只读隔离证明。
6. 8002 已重启并监听 `0.0.0.0`，实际 yy 项目只读检查确认 context 成功、直接 Chat 和 Send 可用、旧入口及字段选择器消失；原有候选阻塞仍使 Start Workflow 禁用，未修改实际项目。8001 未重启。

## 证据与复现

所有日志、脚本和截图位于 [workflow-dsl-chat 证据目录](2026-10-26-workflow-dsl-chat)。重点证据：[真实回复](2026-10-26-workflow-dsl-chat/provider-responses.json)、[Provider session 与权限快照](2026-10-26-workflow-dsl-chat/provider-proof.json)、[新 YAML](2026-10-26-workflow-dsl-chat/result.json)、[Diff 截图](2026-10-26-workflow-dsl-chat/real-diff.png)、[实际 8002 Raw](2026-10-26-workflow-dsl-chat/live-raw.png)。

真实验证脚本为同目录 `server.py` / `real-chat.mjs`，从 worktree 根以 `PYTHONPATH=src:.` 运行 server，传入新的 `/tmp/zf-<purpose>-<UTC>/` 目录。server 使用 8004；Docker 使用 `mcp/playwright:latest --network host`，挂载 workspace 和该证据目录到 `/evidence`，执行 `node /evidence/real-chat.mjs`。若目标端口被占用，需同步修改两份脚本。fixture token 仅用于隔离测试。

完成后两个隔离 server 均已关闭，使用 EventWriter 记录 `simulation.done` 并删除其临时状态。清理明细见 [cleanup.json](2026-10-26-workflow-dsl-chat/cleanup.json)。8002 保留供用户查看。
