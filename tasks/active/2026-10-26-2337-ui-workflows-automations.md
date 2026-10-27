# Workflows 与 Automations 排版

> 状态: active

用户已批准全工作区 UI 优化。本项范围：请求列表和详情可读性；自动化目录、控制/报告/历史与分析折叠。

设计：[189 全工作区排版扩展](../../docs/design/189-gr8r-ui-alignment.md#2026-10-26-全工作区排版扩展)。

- 实施设计 -> verify: 桌面与移动截图、真实数据字段和交互保持。
- 验证行为 -> verify: 前端 typecheck/build、影响域单元测试与 Docker Playwright mock 用例通过。
- 完成交付 -> verify: 8019 预览同步，实现 commit 与归档 hash 齐备。

验收记录：Workflow 审批摘要与可展开图工作台通过，锁定权限不允许批准；Automation 目录与报告/历史/提案切换通过，Run now 仅明确点击后产生一次 mock 动作。

公共验证：`dev-verify.py plan/run --base dev` 通过 docs / web_ui；8 个 instruction contract、63 个前端单元测试文件、typecheck/build、bundle budget、token lint 与 diff 检查通过。全批 26 个不同浏览器用例验收通过；执行命令、分组结果与初轮修复见设计文档“本批验收结果”。证据：`/tmp/zf-ui-pages-20261026-2337/`。8019 已同步验证构建。

限制：真实 Agents 投影接口存在超时，UI 已提供未知状态和重试；后台读取性能不属于本批变更。
