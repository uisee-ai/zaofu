# Z04 命令权益与接管退出合同

> 状态: done — `479e6a15 feat: enforce managed workspace access execution and accounting`

阶段：P1/P3。实施依赖：C07/C08 已发布的预算/命令合同；Z01。两端接线后联合验收，不互等整项 done。

来源：ZaoFu 设计 203 第 4.7、7.4、10.2–10.5 节；用户于 2026-11-09 明确要求初始化 Controller 并基于 203 生成 backlog 实施。

## 范围

- 命令校验后进入 ControlledActionService，沿用幂等/当前性/Gate，工作空间原子名额和预算共享。
- 维护窗口接管与导出退出：排空、用量基线、撤销平台/CPA 凭证，保留项目与本地执行证据。

## 验收

1. 重复投递、执行后回执丢失、撤权与旧部署重放 -> verify: 副作用不重复，不绕过 Kernel，不将未知执行视为成功。
2. 接管、断网、策略到期及获准解绑 -> verify: 仅有效租约内继续，项目数据保留且平台凭证不能带入独立模式。

## 完成证据

精确作用域/代次/策略/期限和执行前人类授权重验接入命令消费者，durable receipt 保证重投不重复副作用。实际 ControlledActionService 执行 Project load/unload、Session stop/resume、Operation cancel；load 等待所属 watcher，未知启动/排空不允许重拉，运行者失踪不能靠 Connector 无限续租。drain、快照导出、当前权限下载和已获批 external 解绑已实现。

step -> verify: 实际 CAS Project 启停入口和命令重投、授权变化、未知执行、导出 credential/symlink/hardlink/半成品、拒绝自行解绑等测试通过。真实 Keycloak/Controller 联调排空，两份机器身份撤销、租约释放、财务结清后以安装签名获得最终释放，项目数据保留。

## 联合验收与边界

- 两端对应实现：Controller `3579923 feat: complete managed runtime authorization and release contracts`；冻结联调源码 `61c4a211b2bef713b60bf2bcee79270023490e9c`，联调时工作树 clean。
- 命令与详细验证见 [204 managed Runtime 接线](../../docs/impl/204-controller-managed-runtime.md#本批验证记录)。末轮 managed 37 passed；Controller 真实 PostgreSQL 回归 31 passed；sentinel 53、三流 smoke 10、serial 10、前端 typecheck/unit 和变更文件 Ruff/diff 检查通过。
- broad deterministic 首次 15,207 passed、20 skipped、7 failed；本批 observed 移除回归已修复并定向通过。剩余 6 个遗留失败在干净分支基点 `489d92b3b7a9a204b1df505701a3b712658722fd` 复现，具体缺失工具/prompt 和 Feishu readiness fixture 原因见实施记录。未宣称全套全绿。
- Q01 保留生产集群/Gateway/TLS/网络/沙箱、邮件 OTP/MFA、真实 CPA、备份/灾备和容量资格。个人 CLI 在途 tokens 可超预留，按实际结算并依 overrun 策略冻结后续调用；订阅金额未知。本项完成不等于生产发布。
- 在 `wip/codex-20261110-controller-runtime` 本地提交；未合并 dev、未 push。

实施边界见 [接入计划](../../docs/impl/201-controller-integration-plan.md)。前序独立切片 [Z01a](2026-11-10-1226-z01a-workspace-connector.md)、[Z03a](2026-11-09-1512-z03a-observation-connector.md)、[Z03b](2026-11-09-1549-z03b-native-codex-usage.md) 保留各自历史证据。
