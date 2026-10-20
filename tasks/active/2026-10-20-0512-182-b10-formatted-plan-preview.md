# 182-B10 · 完整Plan的格式化Markdown阅读

> 状态: active
> 来源: 设计182及本轮用户批准的补充修复；沿用独立worktree，不自动批准产品Plan。

## 范围

- Plan专用有界Markdown分页，保留标题/列表/表格/代码块，不提高聊天消息全局保护阈值。
- 支持全文下载、页间导航与内容身份切换；危险大块保留局部保护，不丢原文。
- 真实Plan、极长Unicode及Markdown结构均需覆盖；Preview保持只读。

## 验收

- 先复现当前长Plan无格式 → verify: Docker真实API/React的标题/表格断言RED。
- 实现后 → verify: 单元测试原文精确重建、跨页表格/围栏；build/typecheck和Docker桌面/手机GREEN。
