> 状态: active

# ZF-SPEC-001 中立交接与执行资格

用户于2026-11-14明确授权基于 spec 在新 worktree 实施；覆盖 F04/F05/F08。
基线：dev `0625d001`；spec SHA256：`f5a033210875e90fe059236974ca0c4ff2737b7b7fbbf6b7c62f6238bce8c52a`。
来源：`docs/spec/zaofu-continuity-and-handoff.spec.md`；207–209 是设计背景，非目标不自动扩大。

## 验收

- 保存/交付/接收分别可见；正文和身份校验；暂停保留、旧写入隔离、原成果保全；Provider 兼容资格独立说明。 -> verify: 真实 caller + 定向回归和异常负对照，逐项留原始命令/退出码；已有能力标记已验证保留，不伪称新实现。
- 影响面验证 -> verify: 按 AGENTS.md 执行后端/共享合同/mock E2E 或前端构建与 Docker 浏览器对应层，不用截图替代业务断言。
- 完成对账 -> verify: 记录代码位置、测试证据、资格限制及未完成项；未验收不标 done。

## 执行记录

本轮：检查点采用当前 Task workspace；身份/正文损坏拒绝 fallback；旧 worker 未退出禁止重新绑定。

验证：checkpoint 系列；writer-fence-red.log 2 个 RED，writer-fence-green.log 51 passed。

2026-11-15 增量：Task Pipeline 不可变交接正文、必读投递、认证接收回执、重启重放、
结果准入补证、恢复命令与 Dashboard 状态已接通。601 项影响闭包、53 sentinel、10 flow smoke、
3 项 Docker 浏览器验证通过。详见 [第二批对账](../../reports/continuity-handoff-20261115-review.md)。
交接实现 commit：`88a7563d feat: persist task handoffs and require authenticated receipt`。

未关闭：任意会话失效的便携交接、Codex/Claude Code 真实接力与物理 writer 隔离；不因新增回执标整项 done。

对账报告：[207–209 / Spec 覆盖](../../reports/continuity-handoff-20261114-review.md)。
实现入口：[实现记录](../../docs/impl/continuity-handoff-spec-001.md)。
首批实现 commit：`2313bf61 feat: harden task continuity and preserve handoff evidence`；未 merge / push / 部署。

## 2026-11-15：剩余规格与真实验证

承接用户本轮授权的 [ZF-SPEC-002](../../docs/spec/zaofu-continuity-remaining-qualification.spec.md) R07/R08/R10；原验收与状态 active 保留。

跨 Provider、旧 writer 物理隔离、全入口交接资格及 SOP/子调用消费继续保留；Claude 接管增强按用户偏好较低优先级。

实现与验证摘要见 [实现记录](../../docs/impl/continuity-handoff-spec-001.md)，本地完整 [缺口与对账报告](../../reports/continuity-qualification-20261115-review.md)。不把局部真实资格当整项完成。
