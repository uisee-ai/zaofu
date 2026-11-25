> 状态: active

# Agent 持久交接与无人交付验证

来源为 [216 规格](../../docs/design/specs/216-durable-agent-mailbox-and-handoff.md)、[215 实施](../../docs/impl/215-oa-stage-team-implementation.md) 及两轮实跑复盘。测试授权为本次用户请求，限定隔离项目和本地交付。批准后不得将临场修复的运行回填为无人通过。

- 消息身份、受众、原子接受与消费者隔离 → verify: AC-01 至 AC-05、AC-09、AC-11、AC-12 的并发、越权和重启对照。
- Worker/OA 真实交接与未答责任 → verify: AC-06 至 AC-10 的生产派发、丢失 wake、未知发送、提问者换代及原验收门。
- 215 工作台、迁移与归档 → verify: AC-13 至 AC-15 的历史快照、访问范围、停用排空和 sidecar 缺件。
- 无人资格计量及诊断收尾 → verify: 按集成凭据计任务、按实际受控动作记操作员介入；父 Run 的终态只处置其诊断及后继反思。
- 冻结可复验源码与本地 8×8 混合产品 → verify: 独立业务断言、候选、Judge、交付和实际介入记录分别保留。报告明确区分 mock、真实 provider 和资格结论。

## 最终验收

216 的 10 项 P0、5 项 P1 均有实施入口和工程证据。实现从 `6dae7720 feat: bind durable agent handoffs to current obligations` 起，最近的集成修复为 `e5ba0235 fix: validate plan source files before workflow approval`。当前分支包含实际 Worker/OA 换代交接、Judge 的真实证据正负对照、Docker 工作台、故障与权限边界验证。[条款与验证](../../docs/impl/216-durable-agent-mailbox-implementation.md)

真实 8×8 验证已经执行。最终 C 使用 `ed208dd2`，结束于正式 Owner 决策。业务派发、独立验收和集成皆为 0/16。C、A 和 B2 均未开启批准后的无人窗口。原 B 因测试助手缺少特定重派批准而排除。C 的实跑后修复已在冻结输入副本和公开入口验算。该修复的完整无人业务交付，以及真实 Claude/stream-json 资格仍待验证。本项保留 active，用于记录尚未取得的真实资格。[C 资格与阶段证据](../../reports/spec216/real-8x8/C/final-qualification.json) · [原 Run 断点](../../reports/spec216/real-8x8/C/parked-checkpoint.json)
