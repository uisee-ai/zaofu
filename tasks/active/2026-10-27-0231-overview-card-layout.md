# Overview 独立指标卡与执行顺序

> 状态: active

用户在本批执行中追加批准：四张指标卡恢复独立边框与间距，Execution Flow 移至左列首位，直接位于 Work Queue 所在指标行下方。

设计：[189 Overview 校准](../../docs/design/189-gr8r-ui-alignment.md#2026-10-27-overview-卡片与执行顺序校准)。

- 卡片布局 -> verify: Playwright 量测桌面卡片间隔至少 15px、四边 1px 边框及窄屏边框保持。
- 阅读顺序 -> verify: Execution Flow 为左列首块，与 Work Queue 左对齐并相隔 16px。
- 回归 -> verify: Overview pulse-first、指标值/跳转、空态测试及 build/typecheck 通过。

验收完成：构建/typecheck、docs/web_ui 验证闭包、受影响浏览器检查通过。本批累计 55 个不同浏览器用例通过；初轮测试夹具和旧几何断言已修正复验。日志、命令和证据见设计 190 的实施与验收记录；预览 8019 已同步。
