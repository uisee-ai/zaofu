# Plan 选区引用 Agent

关联：[187 实施说明](../impl/187-ai-sdlc-graph-workbench.md)。根据用户对文档引用交互的要求，取消每个段落旁常驻的「引用编辑」和预留空白。改动在 `wip/codex-20261026-workflow-graph-ui`，8002 已提供新构建；没有 commit/merge。

## 操作与边界

Inbox 先点击预览顶部「引用编辑」，Workflow 直接打开 Review Plan。选中正文后出现「引用到 Agent」，点击后将引用交给与 YAML 共用的小对话框。只保留一个全局 Agent 入口供重开对话。悬浮按钮在滚动、缩放、清空选区或 Esc 后收起，且限制在视口内；选择行为与确认引用分开，临时选择不会覆盖聊天中的引用。候选待应用、Agent 处理中、无权限或非待审状态不开放新引用入口。

原文绑定机制不变：格式化 Preview 使用 Markdown AST 的完整块源区间（保留格式标记），Raw 支持精确字符区间。后端继续校验文档摘要与 Unicode 区间，并保持引用外正文不变。Apply 沿用受控重新规划，不批准 Plan。

## 验证

- 前端 build/typecheck/bundle check 与自动 Web 模型单测通过：[build](2026-10-27-plan-selection-quote/build.log)、[verification](2026-10-27-plan-selection-quote/verification.log)。本次仅 UI 改动，未运行后端 pytest。
- Docker Playwright：Inbox **11 passed**，覆盖引用对话、Diff、Apply 后审批锁、默认阅读、附件与迟到响应；Workflow **2 passed**，覆盖 Raw/Apply/replan、格式化引用、重复段落与 Unicode 绑定、Esc/滚动收起、手机宽度和已审批状态。测试中的 Provider/Apply 为显式替身，不作为真实 Provider 证据：[Inbox](2026-10-27-plan-selection-quote/inbox-browser.log)、[Workflow](2026-10-27-plan-selection-quote/workflow-browser.log)。首轮 Inbox 夹具缺少独立 workspace 的 onboarding 初始化，停在设置向导；补齐夹具准备后完整重跑通过，未修改产品绕过失败。
- 真实 `ty-review` / `evt-38c86009183e`：鼠标拖选原文，浮出引用入口，点击聚焦 Agent；真实 headless Agent 将“用户打开一个静态页面即可计数”替换为“用户打开计数器页面即可开始计数”。完整候选等于单次指定替换，审批在候选期间锁定，Apply 可用。随后 Discard，原 Plan 正文与 pending 状态不变。零 Apply、零批准、零旧 `chat-orchestrator` 调用；本轮真实验证不重新启动工作流。[结果](2026-10-27-plan-selection-quote/result.json)、[候选](2026-10-27-plan-selection-quote/real-candidate.json)、[悬浮入口](2026-10-27-plan-selection-quote/02-selection-action.png)、[真实 Diff](2026-10-27-plan-selection-quote/04-real-agent-diff.png)、[手机](2026-10-27-plan-selection-quote/05-mobile-agent.png)。

使用 Docker `mcp/playwright:latest --network host`，未安装宿主浏览器。8003/8004 为独立夹具，完成后记录 `simulation.done`、停止服务并删除临时状态；真实 ty-review 和 8002 保留供审阅。原 ty 的历史故障状态见[此前记录](2026-10-27-0016-autoresearch-context-isolation.md)。

入口：[ty-review Plan](http://10.0.169.194:8002/?project=ty-review-42cb2551&page=inbox&plan=evt-38c86009183e)。
