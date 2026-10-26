> 状态: done — `d142c39c fix: restore original workspace navigation`

# 恢复 ZaoFu 侧栏菜单排列

来源：用户明确修订已批准 UI 批次；[设计 189](../../docs/design/189-gr8r-ui-alignment.md) 的侧栏范围修订。

- 恢复项目选择器优先、Workspace / Monitoring / Channels / System settings 原排列 → verify: 桌面与移动端浏览器读取菜单顺序、确认无 Projects 视图次级导航。
- 保留右侧 Tasks 对齐结果 → verify: 既有 Board 几何、三视图交互与 Inbox / Settings 浏览器用例通过。
- 更新预览并提交 → verify: dev-verify、前端生产构建通过；8019 可访问新版本，工作树干净。

验收：`python scripts/dev-verify.py plan --base dev` / `run --base dev` 通过 docs / web_ui 闭包（8 个 instruction contract、前端 typecheck / unit）；Vite 生产构建与 bundle budget 通过（最大 443067 bytes）。既有 `ui-alignment.spec.ts` + `kanban-toolbar.spec.ts` 7/7 通过；最后的紧凑密度修正再运行 General preferences 1/1 通过。Docker Playwright 专项检查确认原菜单顺序、项目选择器优先、桌面 2px 选中边线、移动端跳转关闭菜单，以及舒适 30px / 紧凑 26px 行高；无业务写请求或 page errors。证据：`/tmp/zf-ui-navigation-20261026-1555/`。

最终构建已更新至 http://100.64.0.5:8019/ ，临时 8020 预览已关闭。
