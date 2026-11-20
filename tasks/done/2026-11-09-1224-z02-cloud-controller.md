# Z02 managed 访问与运行准入

> 状态: done — `479e6a15 feat: enforce managed workspace access execution and accounting`

阶段：P0/P3。实施依赖：C02b；C04/C07 已发布的访问/权益合同；Z01。两端接线后联合验收，对外发布仍要求 C02 完整身份资格。

来源：ZaoFu 设计 203 第 4.6、8、10.1、10.3、10.4 节；用户于 2026-11-09 明确要求初始化 Controller 并基于 203 生成 backlog 实施。

## 范围

- Web 的可信入口与访问上下文校验、平台撤销/归属授权；关闭 managed 模式的本地 trusted 绕过。
- 在新 Run/Operation、启动/恢复与复用会话路径接入权益检查，覆盖 Workflow/Kanban/Channel/helper。

## 验收

1. 同一 Workflow 在双模式执行 -> verify: Kernel 状态/Gate/产物合同一致，managed 多一道有效平台授权与预算检查。
2. 配置篡改、凭证丢失、跨项目并发与长连接撤销 -> verify: 无降级绕过、共享名额不重复、关键调用入口均受管。

## 完成证据

Gateway 上下文的签名与请求绑定、当前人类 Session/owner/版本重验、Origin、WS 撤权与本地 trusted 绕过关闭已接实际 Web。新 Run/start/resume，以及 headless、stream-json、tmux dispatch/compact、native terminal、Evolution/Autoresearch helper 在实际调用边界执行共享名额和预算准入，复用 Session 每轮重新授信。

step -> verify: `test_controller_managed_kernel.py` 在 standalone/managed 执行相同 Workflow 的 writer → verifier → test.passed，产物/Gate 证据一致；managed 增加调用前授信和用量结算。37 项 managed 末轮测试通过，真实 HTTP 联调验证跨 Project 单 Agent 名额与额度耗尽拒绝。

## 联合验收与边界

- 两端对应实现：Controller `3579923 feat: complete managed runtime authorization and release contracts`；冻结联调源码 `61c4a211b2bef713b60bf2bcee79270023490e9c`，联调时工作树 clean。
- 命令与详细验证见 [204 managed Runtime 接线](../../docs/impl/204-controller-managed-runtime.md#本批验证记录)。末轮 managed 37 passed；Controller 真实 PostgreSQL 回归 31 passed；sentinel 53、三流 smoke 10、serial 10、前端 typecheck/unit 和变更文件 Ruff/diff 检查通过。
- broad deterministic 首次 15,207 passed、20 skipped、7 failed；本批 observed 移除回归已修复并定向通过。剩余 6 个遗留失败在干净分支基点 `489d92b3b7a9a204b1df505701a3b712658722fd` 复现，具体缺失工具/prompt 和 Feishu readiness fixture 原因见实施记录。未宣称全套全绿。
- Q01 保留生产集群/Gateway/TLS/网络/沙箱、邮件 OTP/MFA、真实 CPA、备份/灾备和容量资格。个人 CLI 在途 tokens 可超预留，按实际结算并依 overrun 策略冻结后续调用；订阅金额未知。本项完成不等于生产发布。
- 在 `wip/codex-20261110-controller-runtime` 本地提交；未合并 dev、未 push。

实施边界见 [接入计划](../../docs/impl/201-controller-integration-plan.md)。前序独立切片 [Z01a](2026-11-10-1226-z01a-workspace-connector.md)、[Z03a](2026-11-09-1512-z03a-observation-connector.md)、[Z03b](2026-11-09-1549-z03b-native-codex-usage.md) 保留各自历史证据。
