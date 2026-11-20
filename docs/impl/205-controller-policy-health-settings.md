# 审批开通、Provider 健康与集中配置接线

本批补齐 [203 Workspace Connector](203-controller-workspace-connector.md)、
[204 managed Runtime](204-controller-managed-runtime.md) 与设计 203/204 的三项复盘缺口。
Controller 的 C16/C17 位于独立项目；本仓库负责 Z05/Z06。

## 审批后的自动开通

Controller 审批云端申请时记录一次性 pending 授权。Runtime 的认证 control poll
验证当前部署、代次、用户准入、原申请和近期合格版本心跳后发布策略，随后实例校验并
签收。Gateway 继续要求策略版本和应用部署匹配。人工改过策略、撤权、停机、失效心跳
不触发自动重授。已有旧申请不自动回填这一授权，仍走显式策略更新。

## 集中配置合同

`limits.runtime_settings` 使用 `zfc.runtime-settings.v1`，与现有 policy version 一起分发。
`runtime_settings_digest` 为排序、紧凑 JSON 的 SHA-256；应用回执带同一摘要。
Controller 只向心跳声明 `workspace-runtime-v2` 的实例下发，旧消费者不会被视为已应用。

| 字段 | 当前范围与消费者 |
| --- | --- |
| interval_seconds | 1–30 秒；watch / cloud serve 每轮读取；维持现有 45 秒心跳资格窗口 |
| batch_size | 1–32；真实 WorkspaceTransport 上传批次 |
| diagnostic_sample_rate | 0–1；OTLP 健康诊断采样；不采样用量账本 |
| mode | self_managed_cli / self_managed_api / managed_cpa |
| request_tokens / request_money / price_version | 每次执行预留、价格绑定；中心同步验证请求上限与模式 |
| request_seconds | 10–86400 秒；调用期限及中心预留上限 |

`zf.yaml` 保留安装身份、接入端点、本地许可和默认值；策略是授权中心的持久化投影，
不改 Project 配置、Kernel 或事件事实。未下发设置时保持现有行为。中央表单未勾选配置
表示不修改已有设置，恢复默认值需显式下发对应值。

外部实例在 WorkspaceConfig 的 `workspace.controller.allowed_modes` 中列出允许模式，
默认仅允许本机 `mode`。云端由经审核 RuntimeProfile 的 `allowed_model_access_modes`
生成只读配置，第一个为初始模式。允许模式不等于已配置上游凭据；仍由实例所有者
配置 CLI 登录/API 或经资格验证的 CPA 连接，Controller 不操作个人登录文件。

模式变化需要本地同意、无在途执行/未知账务责任、各 Project（含退出范围的 Project）
与安装 outbox 排空且没有预留容量。与 Provider admission 共用本地锁防竞态。
等待时回执为 `local_consent_required` / `execution_drain_required`，旧回执继续上传和结算，
下一轮重新尝试；新执行仍受中心最新策略的应用状态约束。错误摘要、回滚、同版本冲突
不覆盖上一次已应用策略。

模式切换只追加 outbox 归属时间边界，旧消息体和 ID 不变；迟到原生用量按原始
`usage_timestamp` 归属。已捕获旧配置的调用不能在新配置下申请预算。未知 usage
仍为未知，不通过切换模式抹平未结账目。

## Provider 健康

受管 headless/helper/stream/tmux/terminal 调用完成及仅观测 Session 生命周期产生健康
事实。健康来源是实际调用结果，不主动探测账户或读取个人凭据。只识别结构化错误码：
成功、401、明确撤销、429、明确额度耗尽、5xx；超时、取消、无法确认的文本错误为 unknown。
原始错误、提示词、密钥与正文不进入报告。成功执行不代表还剩多少额度。

私有 SQLite Connector ledger 保存最新报告和单调版本；投递失败保留，丢失 ACK 重发
同版本同时间，超过 300 秒不续报健康。中心拒绝时间倒退和来源冲突，过期或已撤销部署
显示 unknown。绑定包含 deployment/project/mode/provider 摘要，避免身份轮换后的版本
冒认。Runtime 不冒充 CPA 号池健康来源，CPA 仍由专属 connector 报告。

`controller workspace sync` 返回 `policy_error` 和 `health_delivery_failures`，供运维区分
策略等待与计量传输；诊断失败不会阻止必要回执排空。standalone 未注册时不依赖这些服务。

## 验证边界

测试覆盖实际策略/预算/健康 HTTP 接口、配置回滚与摘要、本地许可、在途排空、跨模式
迟到用量、真实 Provider 包装器、实际 helper 子进程、OTLP 消费及计量不采样。
隔离 Compose 联验使用 PostgreSQL 17、Keycloak 26.8.2 和 Controller HTTP；模型回执
为测试数据，非本次真实订阅消耗。控制台使用 Docker Playwright。

Q01 保留目标生产集群、TLS/网络/沙箱、邮件/MFA、真实 CPA、备份恢复及容量验收。
本批不代替该生产资格，也不把订阅 token 换算为 API 实际账单。

本批验证记录：Runtime 影响闭包 291 passed；后续针对 helper/等待调度和诊断故障隔离的
回归 91 passed、最终新增合同 18 passed（存在重叠，不相加作为唯一用例数）。
`dev-verify plan --base dev` 因包含尚未合并的前序 Z01–Z04 差异选择 897 个测试文件；
本批使用上述精确闭包，没有重复运行已记录的全量遗留失败。
`dev-verify run --base dev --workers 4 --skip-tier deterministic` 执行 serial 10、
sentinel 53、mock E2E 10，以及前端 typecheck/unit，均通过。
Ruff、diff 检查和两仓库实际 HTTP 联验通过；全量 pytest 本批未执行。

最终模式切换与本地解绑回归：2 passed；观察模式移除项目无需读取已移除配置。
