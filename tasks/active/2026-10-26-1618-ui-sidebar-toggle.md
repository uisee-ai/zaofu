# 品牌区与侧栏收放对齐 GR8R

> 状态: active

用户已授权在 UI worktree 实施：左上角品牌区、侧栏收起与展开参考 GR8R，同时保留 ZaoFu 原菜单排列。

设计依据：[189 品牌与侧栏收放](../../docs/design/189-gr8r-ui-alignment.md#2026-10-26-品牌与侧栏收放)。

- 品牌调整 -> verify: ZaoFu、22px 字标、13.5px Geist、原菜单顺序不变。
- 桌面收放 -> verify: 252px / 52px、图标导航、刷新持久化、禁用存储仍可用。
- 移动抽屉 -> verify: 遮罩 / Escape / 导航关闭、焦点恢复、背景隔离、断点切换。
- 页面回归 -> verify: 前端 typecheck / build、选定单元测试与 Docker Playwright；8019 预览更新。

证据：`/tmp/zf-ui-sidebar-20261026-1618/`。真实后端仅只读查看。

验证已完成：

- `python scripts/dev-verify.py plan --base dev` / `run --base dev`：docs / web_ui，通过 8 个 instruction contract、Web typecheck 和 Web unit；未运行全量后端 pytest。
- `node node_modules/vite/bin/vite.js build --outDir /tmp/zf-ui-sidebar-20261026-1618/dist`（web 目录）：通过；bundle check 39 chunks，最大 446084 / 500000 bytes。
- Docker Playwright：`workspace-rail.spec.ts ui-alignment.spec.ts kanban-toolbar.spec.ts --workers=1`，10 passed。新增用例覆盖菜单顺序、收放几何、图标导航、刷新、深色、存储失败及移动抽屉键盘 / 遮罩 / 断点行为。
- `npm run lint:tokens --prefix web`、29 样式字号扫描、`git diff --check` 通过。
- 生产构建完成测试与截图复核后更新 8019；临时 8020 服务已停止。
- 8019 真实预览只读复核：展开 252px、收起 52px、page errors 0、业务写请求 0，见 `live-result.json` 与 `live-*.png`。
