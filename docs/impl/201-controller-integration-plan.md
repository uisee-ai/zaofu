# 云端 Controller 接入实施

> 状态: active

来源：[203](../../../zaofu/docs/design/203-cloud-workspaces-auth-and-usage.md)。总体任务与未决输入见 [Controller 实施计划](../../../zaofu-controller/docs/impl/01-cloud-delivery-plan.md)。

本分支从 dev 创建，负责 Z01 Connector、Z02 managed 准入、Z03 可靠用量/会话上报和 Z04 受控命令/预算接入。保持单机模式、Kernel/Store/Event 权威与所有调用入口覆盖。

- [Z01 实例 Connector 身份与协议接入](../../tasks/active/2026-11-09-1224-z01-cloud-controller.md)
- [Z02 managed 访问与运行准入](../../tasks/active/2026-11-09-1224-z02-cloud-controller.md)
- [Z03 用量和会话持久 outbox](../../tasks/active/2026-11-09-1224-z03-cloud-controller.md)
- [Z04 命令权益与接管退出合同](../../tasks/active/2026-11-09-1224-z04-cloud-controller.md)
