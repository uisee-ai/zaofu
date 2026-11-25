> 状态: active

# Agent 持久交接与无人交付验证

来源为 [216 规格](../../docs/design/specs/216-durable-agent-mailbox-and-handoff.md)、[215 实施](../../docs/impl/215-oa-stage-team-implementation.md) 及两轮实跑复盘。测试授权为本次用户请求，限定隔离项目和本地交付。批准后不得将临场修复的运行回填为无人通过。

- 消息身份、受众、原子接受与消费者隔离 → verify: AC-01 至 AC-05、AC-09、AC-11、AC-12 的并发、越权和重启对照。
- Worker/OA 真实交接与未答责任 → verify: AC-06 至 AC-10 的生产派发、丢失 wake、未知发送、提问者换代及原验收门。
- 215 工作台、迁移与归档 → verify: AC-13 至 AC-15 的历史快照、访问范围、停用排空和 sidecar 缺件。
- 无人资格计量及诊断收尾 → verify: 按集成凭据计任务、按实际受控动作记操作员介入；父 Run 的终态只处置其诊断及后继反思。
- 冻结可复验源码与本地 8×8 混合产品 → verify: 独立业务断言、候选、Judge、交付和实际介入记录分别保留。报告明确区分 mock、真实 provider 和资格结论。
