> 状态: active

2026-11-15 续作：实际 hook-recv CLI 保存收件后丢通知，重建控制器只核对一次并保持暂停；
损坏账本阻止接续。补认证前任记录读取与 SOP 方法；不冒称真实模型完整恢复、全局历史
或全部副作用路径已关闭。证据：[续作报告](../../reports/continuity-next-20261115-review.md)。

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

未关闭：仍需未接入 Provider、宿主监督独立故障域、未知外部效果自动核查及全局历史/子调用消费旅程。

对账报告：[207–209 / Spec 覆盖](../../reports/continuity-handoff-20261114-review.md)。
实现入口：[实现记录](../../docs/impl/continuity-handoff-spec-001.md)。
首批实现 commit：`2313bf61 feat: harden task continuity and preserve handoff evidence`；未 merge / push / 部署。

## 后续增量：接续与资源占用

Claude stream-json 现在直接落盘回合打开/关闭事实；丢失内存通知后由 idle replay 核对当前身份并保持同一 attempt 接续。长回合、工具未决、暂停、旧身份和错误最终结果阻止接续。仍缺全 Provider 与宿主独立故障域、未知效果自动核对的整体验收。

验证与限制：[本增量报告](../../reports/continuity-runtime-20261115-review.md)。状态保持 active，不将子能力通过视为整项完成。

## 2026-11-15：剩余规格与真实验证

承接用户本轮授权的 [ZF-SPEC-002](../../docs/spec/zaofu-continuity-remaining-qualification.spec.md) R02/R03/R08/R10；原验收与状态 active 保留。

真实 systemd/tmux 两场景通过：控制器强杀后唯一恢复、resident 不重复、暂停不重启。修复非 passed artifact 结果仍发完成申请，覆盖 Orchestrator 实时/重放负例；原终态 gate 原已拒绝。所有 Task 后续成果、未知效果核对与全局历史仍未关闭。

实现与验证摘要见 [实现记录](../../docs/impl/continuity-handoff-spec-001.md)，本地完整 [缺口与对账报告](../../reports/continuity-qualification-20261115-review.md)。不把局部真实资格当整项完成。

## 2026-11-15：按成功条件收口

用户已批准修正后的实施顺序；本任务保持 active，逐条验收以 [剩余关闭清单](../../docs/impl/continuity-handoff-spec-001.md#2026-11-15按成功条件推进的剩余关闭清单) 为索引。只关闭有真实调用方和适用证据的条款，其他剩余项不因本批提交自动完成。

本轮修正：续接提示唯一 claim/摘要与 provider 收件事实进入 idle 对账，unknown 不盲重发；Run Manager 与 handoff 消费结果。原生 hook 完整故障旅程待验。 详见[修正报告](../../reports/continuity-closure-20261115-review.md)。
