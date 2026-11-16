> 状态: active

2026-11-15 继续验证：实际 recover/index 复现同 Package 内合同变更仍标 current；修复为
校验 Task/operation 当前绑定并报告 requires_rebind，保留原输入与 dispatch。覆盖旧新快照、
既有 generation 保留和恢复 reader 回归；活跃 writer 隔离全旅程仍待验。
见[继续验证报告](../../reports/continuity-validation-20261115-review.md)。

2026-11-15 续作：R01-B 补旧/新合同不可变快照及后继派发引用，首次修订也保留旧正文。
验证旧新正文、重建读取、损坏阻断及脏候选保留；活跃修订/旧批准隔离全旅程仍未完成。
证据：[续作报告](../../reports/continuity-next-20261115-review.md)。

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
首批实现 commit：`2313bf61 feat: harden task continuity and preserve handoff evidence`；未 merge / push / 部署。

## 2026-11-15：剩余规格与真实验证

承接用户本轮授权的 [ZF-SPEC-002](../../docs/spec/zaofu-continuity-remaining-qualification.spec.md) R01；原验收与状态 active 保留。

活跃输入修订、真实大输入语义反例、多次真实 compaction 后业务成果继续保留，未以本轮真实 Verify 替代。

实现与验证摘要见 [实现记录](../../docs/impl/continuity-handoff-spec-001.md)，本地完整 [缺口与对账报告](../../reports/continuity-qualification-20261115-review.md)。不把局部真实资格当整项完成。

## 2026-11-15：按成功条件收口

用户已批准修正后的实施顺序；本任务保持 active，逐条验收以 [剩余关闭清单](../../docs/impl/continuity-handoff-spec-001.md#2026-11-15按成功条件推进的剩余关闭清单) 为索引。只关闭有真实调用方和适用证据的条款，其他剩余项不因本批提交自动完成。

## 2026-11-16：按批准顺序推进真实集成验收

先核验从 v5 派生配置的实际 Reader dispatch 范围、沙箱与预算，
再完成最小 PRD 正常交付，最后分别注入中断/输入变更/补证据。
发现 lane provider_session 的 sandbox 解析未接通，补解析及真实配置回归；
完整 B02 成功条件仍保留，未以配置通过替代恢复成果。
记录：[本批实现与资格边界](../../docs/impl/continuity-handoff-spec-001.md)。

2026-11-16 最小 PRD 现场追加：修复 Candidate blocked/abstained 在 fanout 投影及重放
丢失 schema 后误触发全局 Plan；fixed-input repair 从历史已接纳 sidecar 恢复分类，
可受控撤销显式已停止误派发后继，保留原 Run/候选。Task/B02 整项仍 active，
实际恢复与全旅程证据继续记录到本轮报告，不以单元测试替代完整资格。
