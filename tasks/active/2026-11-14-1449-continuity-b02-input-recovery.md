> 状态: active

# ZF-SPEC-001 任务输入与有界恢复

用户于2026-11-14明确授权基于 spec 在新 worktree 实施；覆盖 F02/F03。
基线：dev `0625d001`；spec SHA256：`f5a033210875e90fe059236974ca0c4ff2737b7b7fbbf6b7c62f6238bce8c52a`。
来源：`docs/spec/zaofu-continuity-and-handoff.spec.md`；207–209 是设计背景，非目标不自动扩大。

## 验收

- 保留条款/共享约束/来源 occurrence；固定恢复边界，完整补读后指向下一步骤；新上下文不得复用旧消费证明。 -> verify: 真实 caller + 定向回归和异常负对照，逐项留原始命令/退出码；已有能力标记已验证保留，不伪称新实现。
- 影响面验证 -> verify: 按 AGENTS.md 执行后端/共享合同/mock E2E 或前端构建与 Docker 浏览器对应层，不用截图替代业务断言。
- 完成对账 -> verify: 记录代码位置、测试证据、资格限制及未完成项；未验收不标 done。

## 执行记录

本轮：新增有限恢复出口、分页 next_action、输入责任/别名/共享约束的 planner 方法。

验证：contracts-final.log；required resume/snapshot/capability；skill parity。

未关闭：仍需真实大输入独立语义反例、活跃输入修订事务与真实多次 compaction 后成果。

对账报告：[207–209 / Spec 覆盖](../../reports/continuity-handoff-20261114-review.md)。
实现入口：[实现记录](../../docs/impl/continuity-handoff-spec-001.md)。
实现 commit：待本批提交后回填；未 merge / push / 部署。
