# Workflow Agent 简洁对话框

关联：[187 实施说明](../impl/187-ai-sdlc-graph-workbench.md)。本次仅调整前端交互，沿用已经验证的 headless DSL 编辑接口；改动位于 `wip/codex-20261026-workflow-graph-ui`，尚未提交或合并。

点击图工具栏或 Raw 中的 Agent，打开同一个原生模态对话框：当前 Workflow 标题、对话记录、底部单一输入框。修改 Diff 位于 Agent 回复中，仅保留 Apply / Discard。Raw 不嵌入聊天区。关闭再打开保留本页面中的对话、输入及待应用 Diff；关闭 Raw 也不销毁聊天状态。输入支持 Ctrl/⌘+Enter，Esc 关闭；项目/请求切换仍隔离上下文，待处理 Diff 仍阻止启动和切换请求。

验证：

- build/typecheck/bundle check、字体 token 检查通过；自动 Web typecheck 和模型单测通过。本次没有后端改动，不重复执行 pytest。
- Docker Playwright 共 8 个当前版本用例通过：最终整组 6 passed，Agent 对话框和串行跳过的 Plan 用例分别补验 1 passed。覆盖桌面/手机、明暗主题、单输入框、关闭重开、待应用 Diff 保留、键盘发送、YAML 更新、启动失败拦截、两个任务创建入口、需求引用修改与 Plan 独立审批。
- 初轮发现引用浮层被侧栏遮挡，已修正 z-index；后续焦点测试补充了原生 dialog 允许 Tab 到浏览器 chrome 的情况，仍断言背景页面控件不会获得焦点。
- 8002 实际 yy 项目只读浏览器检查通过，未向真实项目发送消息或应用修改。当前静态构建已生效，8001/8002 服务进程均未重启。此次 Provider 使用确定性替身；真实 headless 语义验证见前一份 DSL Chat 记录。

日志与截图：[证据目录](2026-10-26-workflow-agent-dialog)。实际界面：[浅色](2026-10-26-workflow-agent-dialog/agent-light.png)、[深色](2026-10-26-workflow-agent-dialog/agent-dark.png)、[手机](2026-10-26-workflow-agent-dialog/agent-mobile.png)；修改卡片：[Diff](2026-10-26-workflow-agent-dialog/fixture-agent-diff.png)。

隔离 fixture 使用 `tests/e2e/scripts/workflow-prepare-server.py`，浏览器使用 `mcp/playwright:latest --network host`，API 绑定 `0.0.0.0`。用例位于 `web/tests/workflow-graph-redesign.spec.ts`、`workflow-preview-review.spec.ts`、`workflow-preparation.spec.ts`、`workflow-proposal-page.spec.ts`。测试结束通过 EventWriter 记录 `simulation.done` 并删除临时状态，见 [cleanup.json](2026-10-26-workflow-agent-dialog/cleanup.json)。

## 后续：Agent 入口可见性

用户反馈看不到 Agent，定位到入口被“未启动候选且有写入权限”条件隐藏。现固定展示 Agent；运行中、未生成候选或无写入权限时仍可打开对话框查看具体原因，输入与 Send 禁用，既有 API 权限及运行快照边界保持有效。8001 仍是 dev 旧界面，8002 提供本 worktree 的新构建。

本次增量验证：build/typecheck/bundle check 与 Review 模型单测通过；Docker Playwright 两项通过，含启动后入口仍可见且输入禁用、正常候选的局部修改及需求引用。对实际 yy 和 tt 项目分别只读浏览器检查，均看到 Agent 并成功打开对话框；yy 可编辑，tt 提示已启动。没有向真实项目发送消息或应用修改。证据为同目录 `visibility-*` 文件，临时 server 和状态已清理。
