> 状态: active

2026-11-15 续作：补清理 effect 已发生但 receipt 丢失的查询后续接，显式重放不扩大资源范围；
注册保留原因供诊断，不自动强删或外发通知。该范围不代替 WebM/上传/归档交付的未知效果
验收。证据：[续作报告](../../reports/continuity-next-20261115-review.md)。

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

## 2026-11-15：剩余规格与真实验证

承接用户本轮授权的 [ZF-SPEC-002](../../docs/spec/zaofu-continuity-remaining-qualification.spec.md) R03/R04/R06；原验收与状态 active 保留。

真实 Terra High 正反例揭露并修复：验证输出身份/引用约束、预设成功模板、夹具口径混淆、上游证据准入绑定和失败证据保留。最终正例完成，反例拒绝结果准入且 Goal 完成数为零。实际固定源码补 WebM/上传/归档、durable cleanup 仍未完成。

实现与验证摘要见 [实现记录](../../docs/impl/continuity-handoff-spec-001.md)，本地完整 [缺口与对账报告](../../reports/continuity-qualification-20261115-review.md)。不把局部真实资格当整项完成。

## 2026-11-15：按成功条件收口

用户已批准修正后的实施顺序；本任务保持 active，逐条验收以 [剩余关闭清单](../../docs/impl/continuity-handoff-spec-001.md#2026-11-15按成功条件推进的剩余关闭清单) 为索引。只关闭有真实调用方和适用证据的条款，其他剩余项不因本批提交自动完成。
