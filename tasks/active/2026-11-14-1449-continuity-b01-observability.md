> 状态: active

# ZF-SPEC-001 运行版本与有效进展

用户于2026-11-14明确授权基于 spec 在新 worktree 实施；覆盖 F01/F07。
基线：dev `0625d001`；spec SHA256：`f5a033210875e90fe059236974ca0c4ff2737b7b7fbbf6b7c62f6238bce8c52a`。
来源：`docs/spec/zaofu-continuity-and-handoff.spec.md`；207–209 是设计背景，非目标不自动扩大。

## 验收

- 核对源版本、加载版本、任务版本；过期 attempt/usage 不刷新当前工作；区分输入、工作记录、验证与准入进展。 -> verify: 真实 caller + 定向回归和异常负对照，逐项留原始命令/退出码；已有能力标记已验证保留，不伪称新实现。
- 影响面验证 -> verify: 按 AGENTS.md 执行后端/共享合同/mock E2E 或前端构建与 Docker 浏览器对应层，不用截图替代业务断言。
- 完成对账 -> verify: 记录代码位置、测试证据、资格限制及未完成项；未验收不标 done。

## 执行记录

本轮：新增运行版本 API/UI、当前 attempt 身份进展及真实准入事件身份桥接。

验证：runtime-final.log、admission-continuation-green.log、acceptance-foundations.log；dashboard 浏览器断言。

未关闭：仍需 worker/任务实际采用版本回执；其他 Provider 的全进展链。

对账报告：[207–209 / Spec 覆盖](../../reports/continuity-handoff-20261114-review.md)。
实现入口：[实现记录](../../docs/impl/continuity-handoff-spec-001.md)。
首批实现 commit：`2313bf61 feat: harden task continuity and preserve handoff evidence`；未 merge / push / 部署。
