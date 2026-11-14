> 状态: active

# ZF-SPEC-001 同版验证、部署与资源收尾

用户于2026-11-14明确授权基于 spec 在新 worktree 实施；覆盖 F12/F13。
基线：dev `0625d001`；spec SHA256：`f5a033210875e90fe059236974ca0c4ff2737b7b7fbbf6b7c62f6238bce8c52a`。
来源：`docs/spec/zaofu-continuity-and-handoff.spec.md`；207–209 是设计背景，非目标不自动扩大。

## 验收

- 自检不可冒充独立验证；补证据不改变源码目标；部署阶段可核对；脏/在用资源拒绝清理。 -> verify: 真实 caller + 定向回归和异常负对照，逐项留原始命令/退出码；已有能力标记已验证保留，不伪称新实现。
- 影响面验证 -> verify: 按 AGENTS.md 执行后端/共享合同/mock E2E 或前端构建与 Docker 浏览器对应层，不用截图替代业务断言。
- 完成对账 -> verify: 记录代码位置、测试证据、资格限制及未完成项；未验收不标 done。

## 执行记录

本轮：task/role/stop 清理保留 ignored/外层成果，stop 保留未合入 commit；已有固定目标/独立证据机制回归。

验证：runtime-final.log、candidate/attempt handoff/goal receipt；contracts-final.log。

未关闭：仍需全部实际资源持有者及 durable cleanup 阶段；真实补视频/上传失败不重入 Impl 的整链验证。

对账报告：[207–209 / Spec 覆盖](../../reports/continuity-handoff-20261114-review.md)。
实现入口：[实现记录](../../docs/impl/continuity-handoff-spec-001.md)。
首批实现 commit：`2313bf61 feat: harden task continuity and preserve handoff evidence`；未 merge / push / 部署。

## 后续增量：接续与资源占用

Role/Task cleanup 与 zf stop 删除工作区期间持独占锁；即使 session 计数为零，有实际持有者仍拒绝删除。未实现 durable cleanup 全阶段事务，未把库级 Task cleanup 测试冒充完整生产收尾旅程。

验证与限制：[本增量报告](../../reports/continuity-runtime-20261115-review.md)。状态保持 active，不将子能力通过视为整项完成。
