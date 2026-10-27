> 状态: done — `d2822449 fix: match GR8R workspace layout and typography`

# GR8R 实测视觉校准

用户批准原 UI 批次后的明确修订：按 Website Redesign → Board、Inbox、Settings / Appearance 与字体排版精确对齐。设计依据：[189](../../docs/design/189-gr8r-ui-alignment.md)。

- 共享框架与字体 → verify: Playwright 1440×1000 的侧栏252、topbar46，实际 Geist loaded，无外框。
- Board 标题/标签/工具栏、列卡片与 List/Table → verify: 参考几何测量和三视图交互断言；授权锁定无写入。
- Inbox 与 General / Appearance → verify: 420 / 220px 分栏、截图、审批/预览与设置持久化回归。
- 响应式与交付 → verify: 窄屏和暗色浏览器回归、build、dev-verify 通过，8019 真实预览更新。

验收已通过：dev-verify 的 docs / web_ui 闭包、build / bundle budget / token 检查、Docker Playwright 8/8。生产预览 http://100.64.0.5:8019/ 已更新并保留运行。参照与最终截图在 `/tmp/zf-ui-parity-20261026-145557/`；未运行真实写动作或 Provider E2E。
