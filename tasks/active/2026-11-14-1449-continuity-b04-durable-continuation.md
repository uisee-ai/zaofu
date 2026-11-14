> 状态: active

# ZF-SPEC-001 持久恢复消费与局部接续

用户于2026-11-14明确授权基于 spec 在新 worktree 实施；覆盖 F06/F09/F17。
基线：dev `0625d001`；spec SHA256：`f5a033210875e90fe059236974ca0c4ff2737b7b7fbbf6b7c62f6238bce8c52a`。
来源：`docs/spec/zaofu-continuity-and-handoff.spec.md`；207–209 是设计背景，非目标不自动扩大。

## 验收

- 丢通知/控制器重启后继续核对义务；未知效果先查询；迟到答复不推进错误 attempt；合法等待不被抢跑。 -> verify: 真实 caller + 定向回归和异常负对照，逐项留原始命令/退出码；已有能力标记已验证保留，不伪称新实现。
- 影响面验证 -> verify: 按 AGENTS.md 执行后端/共享合同/mock E2E 或前端构建与 Docker 浏览器对应层，不用截图替代业务断言。
- 完成对账 -> verify: 记录代码位置、测试证据、资格限制及未完成项；未验收不标 done。

## 执行记录

本轮：新增 idle sweep 回合消费、同 attempt 续接、发送前持久 claim、unknown 责任投影及 work record 推进边界。

验证：runtime-final.log、acceptance-foundations.log、dispatch-callers.log；暂停/工具/审批/求助/旧身份/重启反例。

未关闭：仍需其他 Provider、宿主监督独立故障域、未知外部效果自动核查及全局历史/子调用消费旅程。

对账报告：[207–209 / Spec 覆盖](../../reports/continuity-handoff-20261114-review.md)。
实现入口：[实现记录](../../docs/impl/continuity-handoff-spec-001.md)。
实现 commit：待本批提交后回填；未 merge / push / 部署。
