# Review Plan 主阅读区引用与编辑修复

关联：[187 实施说明](../impl/187-ai-sdlc-graph-workbench.md)。继续在 `wip/codex-20261026-workflow-graph-ui` 修改，未 commit/merge；仅 UI 和前端验证，没有修改服务端控制、Plan 审批或重新规划合同。

## 根因与复现

旧页面把 `PlanReviewReader` 和 `PlanArtifactChat` 作为两个独立阅读区。上方的审批概览及格式化原文没有选区捕获；仅下方第二份原文 `pre` 有引用事件。直接打开 Agent 没有绑定引用，Send 会禁用。前次 E2E 选中了下方 `plan-review-source`，未覆盖用户自然阅读的入口。

在 8002 真实 ty 页面，用浏览器选中审批正文，确认没有引用动作；点击 Agent 输入要求后 Send 仍禁用。本次没有提交该无引用输入。见 [复现断言](2026-10-26-plan-review-entry/before.json)、[阅读选区截图](2026-10-26-plan-review-entry/before-reading-selection.png)、[无引用的回复框](2026-10-26-plan-review-entry/before-agent-no-quote.png)。

## 修改与操作

- Review Plan 默认主阅读区直接读取同版绑定文档；格式化 Preview 选段后点击“引用讨论 / 修改”，或者点击段落右侧铅笔，即可打开同一小回复框。文档下仍有 Agent 入口，缺少引用时明确说明操作方式。
- 格式化选区引用完整语法块，保留 Markdown；原始源位置绑定段落身份，转换为服务端的 Unicode code point 偏移，避免相同文字匹配到错误段落。Raw 可精确选字。引用外正文仍由原服务端合同保护。
- 加载失败显示错误和重试；只读、已处理、正在生成候选或有待应用 Diff 时禁用段落编辑。多个编辑入口沿用同一回复框，打开期间切换段落不会被外部收起逻辑误关。
- 审批概览与附件仍可展开阅读，并明确为只读资料。Agent 提交修改要求后检查 Diff，Apply 仍请求重新规划，等待新版人工审批。

## 验证

Docker 使用 `mcp/playwright:latest --network host`，未安装宿主浏览器。真实 ty 的主阅读区使用原生鼠标拖选并点击引用，真实 headless Agent 返回局部修改候选；全文比对确认只替换请求的一个短语。关闭重开保留 Diff，Discard 后直接点段落编辑也能打开；手机没有横向溢出。测试候选已丢弃，`evt-f0c18ed971c3` 仍 pending，原文未变，没有 Apply 或 Plan approve 请求。见 [真实结果](2026-10-26-plan-review-entry/after.json)、[Agent 候选](2026-10-26-plan-review-entry/real-agent-candidate.json)、[引用截图](2026-10-26-plan-review-entry/after-formatted-quote.png)、[手机编辑](2026-10-26-plan-review-entry/after-edit-mobile.png)。本轮没有重复实际 Apply/replan，前次真实证据仍见 [ty Plan Chat](2026-10-26-1700-ty-plan-chat.md)。

- 前端 build/typecheck/bundle check 通过；`dev-verify.py` 按本次八个 UI/test 路径选择 Web typecheck 与全部模型单测，通过。见 [build](2026-10-26-plan-review-entry/build-final.log)、[verification](2026-10-26-plan-review-entry/verification.log)。没有运行后端 pytest 或 premerge gate，因为本次没有后端改动或合并。
- 语法区间测试覆盖中文、emoji、CRLF、重复段落、多段引用、列表、表格和引用定义；引用不丢标记，位移符合服务器合同。聚焦结果见 [unit-final.log](2026-10-26-plan-review-entry/unit-final.log)。首次使用了测试编译环境不支持的 Node assert 类型，改用本仓库模式；一次命令路径重复 `web/` 未执行成功，正确路径的聚焦运行及自动全模型运行通过。
- Docker Playwright 原有四项通过，覆盖图、窗口适配、YAML 回复框和 Plan Raw/Apply/replan/审批版本绑定。新用例覆盖主阅读区格式化选段、重复段落编辑、Raw 精确选字、加载重试、手机和已审批禁用，也通过。首轮新用例和真实脚本误假设强调节点为 HTML `strong`；实际渲染器使用其他节点，导致定位等待超时。改为对实际阅读段落选区，并用真实鼠标拖选复核。见 [首轮](2026-10-26-plan-review-entry/browser.log)、[新用例重跑](2026-10-26-plan-review-entry/browser-final.log)、[真实重跑](2026-10-26-plan-review-entry/after-final.log)。

隔离 8003 夹具已停止，按配置追加 `simulation.done` 并删除临时目录，见 [cleanup](2026-10-26-plan-review-entry/cleanup.json)。8002 保留最新前端构建，8001 未变，ty 未获批准或启动产品实施。

## 23:41 UTC：入口可发现性补充

用户仍找不到入口。服务健康信息确认 8001 仍在服务旧构建；新版在 8002，但这不证明用户实际打开了哪个端口。提供 ty 的 8002 直达链接，并将仅铅笔图标改为常驻「引用编辑」按钮，补充明确操作提示；手机按钮改放段落下方。build/typecheck/bundle check 通过。Docker 真实 ty 只读检查覆盖文字入口可见、点击带入引用、输入框聚焦、输入后 Send 可用、手机打开和无横向溢出；没有发送要求、Apply 或批准。见 [入口截图](2026-10-26-plan-quote-entry/01-quote-edit-entry.png)、[Agent 打开](2026-10-26-plan-quote-entry/02-agent-open.png)、[检查结果](2026-10-26-plan-quote-entry/result.json)、[build](2026-10-26-plan-quote-entry/build.log)。
