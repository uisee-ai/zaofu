# Z01 实例 Connector 身份与协议接入

> 状态: active

阶段：P0/P2。实施依赖：C01 已发布合同与 fixtures；两端接线后联合验收，不等待 C01 整项 done 才开工。

来源：ZaoFu 设计 203 第 4.7、9.5、10.1、10.2 节；用户于 2026-11-09 明确要求初始化 Controller 并基于 203 生成 backlog 实施。

## 范围

- 在 ZaoFu 内增加部署级 Connector，明确 standalone/managed，不将 Connector 作为语义角色 Agent。
- 机器身份、能力注册、只读项目/会话快照、来源流、ACK/重试接入 Controller 合同；尊重 project.state_dir。

## 验收

1. 从实际 CLI/启动入口调用 -> verify: standalone 无 Controller 依赖，managed 缺身份不降级；伪造 Workspace 和不兼容协议被拒绝。
2. 同一 Workspace 多 Project -> verify: 共享部署接入，快照不含终端正文、token 或模型凭证。

## 当前证据

已实现显式配置、Client Credentials、绑定身份/能力协商、JSON Schema、批次 ACK 和实际 zf controller CLI。真实 Keycloak/Controller 联验通过。自动注册、完整配置/命令通道、租约与凭证轮换仍未完成。

本轮可独立验收切片见 [Z03a](2026-11-09-1512-z03a-observation-connector.md)。

实施边界与仓库关系见 [接入计划](../../docs/impl/201-controller-integration-plan.md)。
