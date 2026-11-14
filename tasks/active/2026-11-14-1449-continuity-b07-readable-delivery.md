> 状态: active

# ZF-SPEC-001 预览与分层阅读

用户于2026-11-14明确授权基于 spec 在新 worktree 实施；覆盖 F14/F15/F16。
基线：dev `0625d001`；spec SHA256：`f5a033210875e90fe059236974ca0c4ff2737b7b7fbbf6b7c62f6238bce8c52a`。
来源：`docs/spec/zaofu-continuity-and-handoff.spec.md`；207–209 是设计背景，非目标不自动扩大。

## 验收

- Kanban/Channel 预览绑定同版输入；慢详情不阻塞其他内容；材料分页和完整原文、未知/删除/无权状态区分；浏览器阅读不计 Agent 恢复。 -> verify: 真实 caller + 定向回归和异常负对照，逐项留原始命令/退出码；已有能力标记已验证保留，不伪称新实现。
- 影响面验证 -> verify: 按 AGENTS.md 执行后端/共享合同/mock E2E 或前端构建与 Docker 浏览器对应层，不用截图替代业务断言。
- 完成对账 -> verify: 记录代码位置、测试证据、资格限制及未完成项；未验收不标 done。

## 执行记录

本轮：Plan 首批分页渐进渲染、底部加载与手动降级、目录定位；pulse 提供版本差异与任务采用未知。

验证：Docker Chromium 9 个浏览器业务断言及 build/typecheck/unit/bundle。

未关闭：仍需全入口约束/版本旅程、SSE 重连/删除语义、后端按章节取文与统一人类材料目录。

对账报告：[207–209 / Spec 覆盖](../../reports/continuity-handoff-20261114-review.md)。
实现入口：[实现记录](../../docs/impl/continuity-handoff-spec-001.md)。
首批实现 commit：`2313bf61 feat: harden task continuity and preserve handoff evidence`；未 merge / push / 部署。
