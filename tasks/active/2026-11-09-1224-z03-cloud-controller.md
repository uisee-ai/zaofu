# Z03 用量和会话持久 outbox

> 状态: active

阶段：P2。实施依赖：C01/C05 已发布的上报/账本合同；Z01。两端接线后联合验收，不互等整项 done。

来源：ZaoFu 设计 203 第 6、7、9.2、9.3、9.5、9.6 节；用户于 2026-11-09 明确要求初始化 Controller 并基于 203 生成 backlog 实施。

## 范围

- 沿现有成本/Provider 遥测来源追加稳定用量关联，可靠 outbox 与游标确认、来源轮转、崩溃恢复及交接。
- 补足各真实调用入口生命周期记录；快照仅对账，原始 Project/Session/Operation 身份保持可追溯。

## 验收

1. 实际 caller 的成功/取消/重试/断流/恢复 -> verify: 成本字段与本地视图一致，上传去重，不用诊断采样代替完整计量。
2. 持久化失败及 ACK 丢失 -> verify: 未确认记录保留、容量压力暂停新增受管调用，代传保留原始归属。

## 当前证据

已实现 EventLog durable observer、SQLite outbox、历史绑定、重放/重启去重、容量恢复、原生短 Session/Operation/usage 与 Terminal 已结算记录。跨仓库真实上报及 Dashboard 已联验。全入口调用前暂停/预算门、源快照水位、未接入 Provider 覆盖及自动保留/隔离处理仍未完成。

本轮可独立验收切片见 [Z03a](../done/2026-11-09-1512-z03a-observation-connector.md)。

实施边界与仓库关系见 [接入计划](../../docs/impl/201-controller-integration-plan.md)。

本机原生 Codex 和期初基线见 [Z03b](../done/2026-11-09-1549-z03b-native-codex-usage.md)。
