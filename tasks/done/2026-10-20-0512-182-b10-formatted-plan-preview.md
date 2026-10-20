# 182-B10 · 完整Plan的格式化Markdown阅读

> 状态: done；实现：`8ce571d feat: add formatted plan reading and semantic review checks`。
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
