# Z02 managed 访问与运行准入

> 状态: active

阶段：P0/P3。依赖：C02、C04、C07；Z01。

来源：ZaoFu 设计 203 第 4.6、8、10.1、10.3、10.4 节；用户于 2026-11-09 明确要求初始化 Controller 并基于 203 生成 backlog 实施。

## 范围

- Web 的可信入口与访问上下文校验、平台撤销/归属授权；关闭 managed 模式的本地 trusted 绕过。
- 在新 Run/Operation、启动/恢复与复用会话路径接入权益检查，覆盖 Workflow/Kanban/Channel/helper。

## 验收

1. 同一 Workflow 在双模式执行 -> verify: Kernel 状态/Gate/产物合同一致，managed 多一道有效平台授权与预算检查。
2. 配置篡改、凭证丢失、跨项目并发与长连接撤销 -> verify: 无降级绕过、共享名额不重复、关键调用入口均受管。

## 当前证据

未执行。前置任务和外部环境未满足时保留本任务，不以 mock 替代上线资格。

实施边界与仓库关系见 [接入计划](../../docs/impl/201-controller-integration-plan.md)。
