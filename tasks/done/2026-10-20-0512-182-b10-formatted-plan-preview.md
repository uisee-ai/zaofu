# 182-B10 · 完整Plan的格式化Markdown阅读

> 状态: done；实现：`8ce571d feat: add formatted plan reading and semantic review checks`。
> 导航回归修复：`3ece438 fix: separate plan navigation from scrolling content`。
> 来源: 设计182及本轮用户批准的补充修复；沿用独立worktree，不自动批准产品Plan。

## 范围

- Plan专用有界Markdown分页，保留标题/列表/表格/代码块，不提高聊天消息全局保护阈值。
- 支持全文下载、页间导航与内容身份切换；危险大块保留局部保护，不丢原文。
- 真实Plan、极长Unicode及Markdown结构均需覆盖；Preview保持只读。

## 验收

- 先复现当前长Plan无格式 → verify: Docker真实API/React的标题/表格断言RED。
- 实现后 → verify: 单元测试原文精确重建、跨页表格/围栏；build/typecheck和Docker桌面/手机GREEN。

## 验证与归档

- 先复现长文标题不格式化、跨页表格丢格式、空白尾页及无换行长围栏崩溃，再修复。
- Docker `mcp/playwright:latest` / Chromium / host network：`plan-preview-content.spec.ts` 5 passed。
- parser/guard 单元、Web 单元层、typecheck/build 通过；37 chunks，最大 432,696 bytes。
- 真实历史 Plan 222,432 bytes、6 页；标题/表格/代码块可见，原文与下载精确一致，
  12 个附件哈希匹配，桌面/手机可达末行；0 JS 错误、0 API 写入。该 Plan 语义状态为 rejected。
- 临时 fixture 服务已正常停止并清理，worktree Dashboard 8002 保持运行。
- B08/B09/B11 的正式语义返工仍需新 generation 授权；B10 关闭不代表本批全部完成。
- [补充记录](../../docs/records/2026-10-20-0528-plan182-semantic-followup.md)。

## 分页导航补验

- `/tmp/10err.png` 的滚动漏字和窄屏按钮错行已在真实 Preview 复现；原 sticky 导航与正文
  共用带 14px padding 的滚动容器。现改为独立导航与正文滚动区，翻页控件同组。
- 三个视口新增布局断言先 3 failed 后 3 passed；键盘滚动后立即换页也归顶。
- 内容/布局/导航 Docker 用例合计 10 passed；前端 build/typecheck、Web 单元层通过，
  文档与指令检查 8 passed。未重跑后端或全量 pytest。
- 真实 Plan 桌面/手机截图及测量：`reports/plan182-pagination/`；0 API 写入、0 JS 错误。
  8003 临时 fixture 已正常关闭并清理；worktree Dashboard 8002 保持运行。
- 此补验不关闭 B08/B09/B11，也不授权新规划 generation。
