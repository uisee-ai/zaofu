# YAML 面板内的小回复框

关联：[187 实施说明](../impl/187-ai-sdlc-graph-workbench.md)。本次在既有 worktree 中收紧前端交互，尚未 commit/merge。

- 图工具栏移除 Agent，Raw 旁不再显示该入口。
- Raw 内移除 Details，原位置改为 Agent。点击后在 YAML 下方展开小回复框，不使用居中模态窗口或背景遮罩。
- 初始仅一个输入框，Agent 回复和 Diff 按需展示；保留 Apply/Discard、快捷键发送、Esc/点击框外收起。关闭 Raw 再打开仍保留当前页面的对话和未发送输入。
- 可选中 YAML 片段引用到回复框。选中内容作为当前版本的消息上下文，Agent 定位已有可编辑字段；字段白名单、版本绑定、编译验证和显式 Apply 仍由原服务检查。需求字段的原结构化引用 API 没有变化。
- 保留必要的 Answer Questions、Prepare Workflow、Run Controls；运行中和无权限场景仍说明不可编辑原因，禁用输入与发送。

验证：build/typecheck/bundle check、自动 Web typecheck 与模型单测通过。Docker Playwright 覆盖 8 个用例：首次 6 passed，发现手机长 Diff 将输入框挤出视口；修复响应式高度和回复框可见性后，复验修改链、手机回复框及串行跳过的 Plan 审批共 3 passed。测试还覆盖 YAML 引用被实际传入消息、Diff 应用后刷新 YAML、启动失败拦截、两个任务创建入口、面板关闭重开和未启动/已启动边界。

对 8002 的真实 yy、tt 项目进行只读浏览器检查：图工具栏没有 Agent，Raw 没有 Details，Agent 回复框位于 YAML 面板内；yy 可编辑时初始高度 190px，tt 运行中高度 258px 并显示冻结原因。没有向真实项目发送消息或应用修改。Provider 与进程启动测试使用既有确定性替身，本次不重复真实 Provider 执行或后端 pytest。

证据：[日志与脚本目录](2026-10-26-workflow-yaml-reply)、[实际 yy 回复框](2026-10-26-workflow-yaml-reply/yy-be38198c-reply.png)、[手机布局](2026-10-26-workflow-yaml-reply/yaml-reply-mobile.png)、[Diff](2026-10-26-workflow-yaml-reply/fixture-diff.png)。

测试使用 Docker `mcp/playwright:latest --network host` 与临时 8003 API。结束后通过 EventWriter 记录 `simulation.done` 并清理临时进程和状态，见 [cleanup.json](2026-10-26-workflow-yaml-reply/cleanup.json)。8001 未变，8002 继续提供 worktree 的最新静态构建。
