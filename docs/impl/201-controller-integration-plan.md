# 云端 Controller 接入实施

> 状态: done — Z01–Z04 Runtime 批次；生产资格另由 Q01 验收

来源：[203](../../../zaofu/docs/design/203-cloud-workspaces-auth-and-usage.md)。总体任务与未决输入见 [Controller 实施计划](../../../zaofu-controller/docs/impl/01-cloud-delivery-plan.md)。

本分支从 dev 创建，负责 Z01 Connector、Z02 managed 准入、Z03 可靠用量/会话上报和 Z04 受控命令/预算接入。保持单机模式、Kernel/Store/Event 权威与所有调用入口覆盖。

实施采用“合同先发布 → 两端接线 → 联合验收”。Z01 消费 C01 已发布的版本化合同；Z02 消费 C04/C07 的访问/权益合同；Z03 消费 C01/C05 的上报/账本合同；Z04 消费 C07/C08 的预算/命令合同。不互相等待整个父任务 done，也不能把单端测试当作联合通过。Controller C02b 提供身份基线，完整对外发布仍要求 C02 邮箱 OTP 与 MFA 资格。

- [Z01 实例 Connector 身份与协议接入](../../tasks/done/2026-11-09-1224-z01-cloud-controller.md)
- [Z02 managed 访问与运行准入](../../tasks/done/2026-11-09-1224-z02-cloud-controller.md)
- [Z03 用量和会话持久 outbox](../../tasks/done/2026-11-09-1224-z03-cloud-controller.md)
- [Z04 命令权益与接管退出合同](../../tasks/done/2026-11-09-1224-z04-cloud-controller.md)

前序 [202 观测接入实现](202-controller-observability.md) 与
[Z03a](../../tasks/done/2026-11-09-1512-z03a-observation-connector.md) 已接线实际 CLI、事件来源、
持久 outbox 和真实 Controller 联验；其完整 managed 责任由本批 204 接线补齐。

后续本机 Codex 与累计基线见 [Z03b](../../tasks/done/2026-11-09-1549-z03b-native-codex-usage.md)。

本轮 Z01–Z04 实现与运行合同见 [204 managed Runtime 接线](204-controller-managed-runtime.md)。
Controller 同步补充当前人类授权、Run readiness、安装退出证明和云端只读配置挂载合同。
两仓库分别提交；未合并到 dev，生产联合资格继续由 Q01 承担。
