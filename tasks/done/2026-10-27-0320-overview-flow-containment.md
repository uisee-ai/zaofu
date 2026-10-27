# Overview Execution Flow 溢出与列对齐修复

> 状态: done
> 实现提交: `9a19e470` fix: contain and align overview execution flow

用户反馈 Execution Flow 中流程内容越出卡片，要求与 Work Queue 对齐。本项属于已批准 UI 批次的后续修复。

设计：[189 Execution Flow 修复](../../docs/design/189-gr8r-ui-alignment.md#execution-flow-容器溢出与同列对齐修复)。

- 复现完整流程 → verify: 修复前 populated pulse 浏览器断言失败，卡片 scrollWidth 超出 clientWidth 24px。
- 同列同宽 → verify: 1440 / 1200 / 900px 下 Execution Flow 和 Work Queue 左边界、宽度差小于 1px。
- 内容容纳 → verify: 1440 / 1200 / 900 / 390 / 360px 下含 WIP、Rework、长阻塞原因的纵向流程无卡片或页面横向溢出。
- 原交互 → verify: 阻塞任务下钻、指标跳转、pulse-first、fallback 与空态浏览器用例通过。
- 构建和交付 → verify: dev-verify plan/run、前端生产构建与 bundle budget、token lint 通过，8019 预览更新，完成实现提交与归档。

证据目录：`/tmp/zf-overview-flow-20261027/`。

验证结果：`python scripts/dev-verify.py plan --base dev` / `run --base dev` 通过 docs / web_ui 闭包（8 个 instruction contract、前端 typecheck、64 个单元测试文件）。Vite 生产构建、bundle budget（40 chunks，最大 450278 bytes）、token lint 与 diff 检查通过。Docker Playwright 的 Overview / pulse-first 共 4 个不同用例通过；完整流程覆盖五种屏宽、无横向滚动、同列同宽及阻塞任务跳转。初轮新增用例把任务页误当作抽屉，已按现有 `openTask` 行为修正并单独复跑通过，没有修改产品跳转。

真实 8019 预览只读复核：Execution Flow 与 Work Queue 均为 266px 宽、x=290，卡片 clientWidth / scrollWidth 均为 264px，无前端异常；该次读取显示 fallback 流程，完整 pulse 的回归使用受控夹具。桌面和移动截图已目视检查。证据：`/tmp/zf-overview-flow-20261027/`，包含修复前失败、最终浏览器结果、构建和验证日志。
