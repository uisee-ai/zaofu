# Plan 引用编辑：统一格式化阅读与全屏布局

前一轮只取消了 Inbox 正文的高度上限，未覆盖 Workflow 的窄侧栏，也未释放 Inbox 的资料侧栏。引用区仍沿用聊天字号，超过 Markdown 单块阈值的长表格会退回纯文本。浏览器复现：旧 Workflow 窗口宽 780px；1440px 宽的 Inbox 引用区域仍只有 1100px。

## 修正

- Inbox 引用模式占满主工具栏下方的宽高，返回审阅后恢复资料侧栏；Workflow Plan 改为全屏，审批与关闭保留在顶部，辅助资料折叠在底部。
- 两个入口都取消 55dvh 限制，正文填满剩余空间、独立滚动；Preview / Raw / Agent 工具栏保持可见。
- 默认 Preview 使用 Plan 阅读页的标题、行高、代码和加粗样式。补齐 Streamdown 的加粗 span 样式，增加段落间距。
- 长表格和代码块复用 `planMarkdownPages` 的分段渲染，保留原始块的 Markdown 源位置。重复表头只用于显示，不改变 Unicode 引用合同。

## 验证

构建、bundle budget、前端 typecheck 和单元测试通过。十九项相关 Docker Playwright 用例最终通过：Inbox 十七项、Workflow 两项；包括三个窗口尺寸、返回审阅、2400 行表格格式化、原文引用校验、Raw、Agent、Apply 与审批锁定。

初次新增测试误以为加粗内容是 `strong` 标签，实际是 `data-streamdown=strong` 的 span；修改定位并验证其 700 字重。长表格引用测试最初双击单元格的空白中心，改为双击文字后通过；原失败日志保留。

8002 实际项目 `ty-review-42cb2551` 的 Inbox 和 Workflow 均完成桌面/手机验证，截图经人工查看。该 Plan 在测试前已 approved，本轮未改变正文或状态，也没有发送真实 Provider 修改请求；引用讨论与 Apply 的动作验证使用隔离夹具。

证据：[旧 Inbox](2026-10-27-plan-reader-unified/before-inbox.log)、[旧 Workflow](2026-10-27-plan-reader-unified/before-workflow.log)、[Inbox 首轮](2026-10-27-plan-reader-unified/inbox.log)、[Workflow 首轮](2026-10-27-plan-reader-unified/workflow.log)、[长表格最终回归](2026-10-27-plan-reader-unified/table-final.log)、[Workflow 最终回归](2026-10-27-plan-reader-unified/workflow-final.log)、[真实结果](2026-10-27-plan-reader-unified/result.json)、[桌面 Workflow](2026-10-27-plan-reader-unified/workflows-1440-formatted.png)、[手机 Inbox](2026-10-27-plan-reader-unified/inbox-390-formatted.png)、[最终构建](2026-10-27-plan-reader-unified/build-final.log)、[前端检查](2026-10-27-plan-reader-unified/verification.log)。

本轮没有提交或合并；随后继续处理用户追加的 DAG Raw Agent 输入问题。
