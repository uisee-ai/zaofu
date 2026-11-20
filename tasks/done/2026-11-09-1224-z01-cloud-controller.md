# Z01 实例 Connector 身份与协议接入

> 状态: done — `479e6a15 feat: enforce managed workspace access execution and accounting`

阶段：P0/P2。实施依赖：C01 已发布合同与 fixtures；两端接线后联合验收，不等待 C01 整项 done 才开工。

来源：ZaoFu 设计 203 第 4.7、9.5、10.1、10.2 节；用户于 2026-11-09 明确要求初始化 Controller 并基于 203 生成 backlog 实施。

## 范围

- 在 ZaoFu 内增加部署级 Connector，明确 standalone/managed，不将 Connector 作为语义角色 Agent。
- 机器身份、能力注册、只读项目/会话快照、来源流、ACK/重试接入 Controller 合同；尊重 project.state_dir。

## 验收

1. 从实际 CLI/启动入口调用 -> verify: standalone 无 Controller 依赖，managed 缺身份不降级；伪造 Workspace 和不兼容协议被拒绝。
2. 同一 Workspace 多 Project -> verify: 共享部署接入，快照不含终端正文、token 或模型凭证。

## 完成证据

部署 Connector 已完成 observed/managed external/managed cloud 三种接线，安装绑定不依赖某一个 Project。实际 `zf controller bootstrap/takeover/serve`、零 Project Web、机器身份轮换/代次、策略 ACK 与命令通道均接通；系统只读配置/本地 pin/密文丢失不退回 standalone。多 Project 保持各自 state_dir，仅上报协议白名单元数据。

step -> verify: `test_controller_workspace.py`、managed Web/zero-project/bootstrap/工作树归属测试通过；独立 Compose Keycloak/PostgreSQL/Controller HTTP 验证零项目注册、两项目、轮换和撤销。

## 联合验收与边界

- 两端对应实现：Controller `3579923 feat: complete managed runtime authorization and release contracts`；冻结联调源码 `61c4a211b2bef713b60bf2bcee79270023490e9c`，联调时工作树 clean。
- 命令与详细验证见 [204 managed Runtime 接线](../../docs/impl/204-controller-managed-runtime.md#本批验证记录)。末轮 managed 37 passed；Controller 真实 PostgreSQL 回归 31 passed；sentinel 53、三流 smoke 10、serial 10、前端 typecheck/unit 和变更文件 Ruff/diff 检查通过。
- broad deterministic 首次 15,207 passed、20 skipped、7 failed；本批 observed 移除回归已修复并定向通过。剩余 6 个遗留失败在干净分支基点 `489d92b3b7a9a204b1df505701a3b712658722fd` 复现，具体缺失工具/prompt 和 Feishu readiness fixture 原因见实施记录。未宣称全套全绿。
- Q01 保留生产集群/Gateway/TLS/网络/沙箱、邮件 OTP/MFA、真实 CPA、备份/灾备和容量资格。个人 CLI 在途 tokens 可超预留，按实际结算并依 overrun 策略冻结后续调用；订阅金额未知。本项完成不等于生产发布。
- 在 `wip/codex-20261110-controller-runtime` 本地提交；未合并 dev、未 push。

实施边界见 [接入计划](../../docs/impl/201-controller-integration-plan.md)。前序独立切片 [Z01a](2026-11-10-1226-z01a-workspace-connector.md)、[Z03a](2026-11-09-1512-z03a-observation-connector.md)、[Z03b](2026-11-09-1549-z03b-native-codex-usage.md) 保留各自历史证据。
