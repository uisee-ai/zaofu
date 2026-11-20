# Z03 用量和会话持久 outbox

> 状态: done — `479e6a15 feat: enforce managed workspace access execution and accounting`

阶段：P2。实施依赖：C01/C05 已发布的上报/账本合同；Z01。两端接线后联合验收，不互等整项 done。

来源：ZaoFu 设计 203 第 6、7、9.2、9.3、9.5、9.6 节；用户于 2026-11-09 明确要求初始化 Controller 并基于 203 生成 backlog 实施。

## 范围

- 沿现有成本/Provider 遥测来源追加稳定用量关联，可靠 outbox 与游标确认、来源轮转、崩溃恢复及交接。
- 补足各真实调用入口生命周期记录；快照仅对账，原始 Project/Session/Operation 身份保持可追溯。

## 验收

1. 实际 caller 的成功/取消/重试/断流/恢复 -> verify: 成本字段与本地视图一致，上传去重，不用诊断采样代替完整计量。
2. 持久化失败及 ACK 丢失 -> verify: 未确认记录保留、容量压力暂停新增受管调用，代传保留原始归属。

## 完成证据

在前序 Z03a/Z03b 基础上，所有受管调用持久化预留和执行责任，以 EventWriter 追加生命周期/完整用量。headless 原生累计差、stream/TUI/terminal/helper 回执接线；外层汇总不重复上传。ACK 回执保留、水位恢复、容量预留、隔离处置、旧来源交接、退出项目的历史补报及价格条件验证已完成。无法证明的 usage/进程/金额保留 unknown，不伪造零值或退款。

step -> verify: 持久化失败恢复、ACK/水位重放、队列容量、已退出 Project 补报与调用方测试通过；真实 Codex headless 经 Controller 结算 14,112 tokens，其中 input 14,104（cache 12,160 已包含）、output 8，订阅费用未知。与现有观测/原生用量联合的聚焦集合 109 passed。

## 联合验收与边界

- 两端对应实现：Controller `3579923 feat: complete managed runtime authorization and release contracts`；冻结联调源码 `61c4a211b2bef713b60bf2bcee79270023490e9c`，联调时工作树 clean。
- 命令与详细验证见 [204 managed Runtime 接线](../../docs/impl/204-controller-managed-runtime.md#本批验证记录)。末轮 managed 37 passed；Controller 真实 PostgreSQL 回归 31 passed；sentinel 53、三流 smoke 10、serial 10、前端 typecheck/unit 和变更文件 Ruff/diff 检查通过。
- broad deterministic 首次 15,207 passed、20 skipped、7 failed；本批 observed 移除回归已修复并定向通过。剩余 6 个遗留失败在干净分支基点 `489d92b3b7a9a204b1df505701a3b712658722fd` 复现，具体缺失工具/prompt 和 Feishu readiness fixture 原因见实施记录。未宣称全套全绿。
- Q01 保留生产集群/Gateway/TLS/网络/沙箱、邮件 OTP/MFA、真实 CPA、备份/灾备和容量资格。个人 CLI 在途 tokens 可超预留，按实际结算并依 overrun 策略冻结后续调用；订阅金额未知。本项完成不等于生产发布。
- 在 `wip/codex-20261110-controller-runtime` 本地提交；未合并 dev、未 push。

实施边界见 [接入计划](../../docs/impl/201-controller-integration-plan.md)。前序独立切片 [Z01a](2026-11-10-1226-z01a-workspace-connector.md)、[Z03a](2026-11-09-1512-z03a-observation-connector.md)、[Z03b](2026-11-09-1549-z03b-native-codex-usage.md) 保留各自历史证据。
