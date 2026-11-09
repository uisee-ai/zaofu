# Controller 观测接入实现

本实现属于 [201 接入计划](201-controller-integration-plan.md) 的 Z01/Z03，
与 [Controller 设计 02](../../../zaofu-controller/docs/design/02-telemetry-ledger-and-observability.md) 联验。
完整 managed 准入、预算、远程命令、接管/退出继续保留在父任务，不等同于启用遥测。

## 入口与兼容

- `zf.yaml` 的 `observability.controller.enabled` 默认 false；单机不建立队列、不访问 IdP。
- 开启后 factory 构造 EventLog 的 durable observer，原事件 fsync 后白名单投影到持久 outbox。
- `zf controller status` 查看积压、隔离、stream、origin 水位和 coverage_since。
- `zf controller sync [--watch]` 是实际网络 caller，Client Credentials 获取短期机器身份，
  校验 `/internal/v1/me` 归属、加载权威 schema、上传批次、验证逐条 ACK。
- 网络依赖放在 controller extra，基础包不隐式依赖 OAuth；命令输出不包含 secret 或正文。
- 不改写 Kernel、TaskStore、SessionStore；Controller 没有本地执行状态的第二套状态机。

配置、秘密文件和机器登记步骤见 [运行手册](../../../zaofu-controller/docs/runbooks/controller-observability.md)。
state 路径从 project.state_dir 解析；持久队列位于其 `controller/outbox.sqlite3`，并非固定 `.zf`。
watch 需由实例进程管理器运行；本次未给 `zf start` 增加自动网络 sidecar。

## 来源与可靠性

| 来源 | 投影 |
| --- | --- |
| agent.usage | 与 cost-record.v2 相同的规范化 token 分类、稳定 sample、累计 series、原事件归属 |
| agent.session.run | 短会话与跨 Task 的 Session 历史、Operation、成功/失败/取消 turn usage；缺失保持 pending |
| operator.session / workflow.operation | 白名单生命周期与已存在的关联 |
| Web Terminal registry / terminal-cost | 持久创建与结束事实、已结算增量、稳定 sample 和原生 Session 关联 |

事件发生时的 deployment/mode 绑定持久化，补采不能把旧来源改成当前部署。
接入前无绑定历史不自动上传，coverage_since 明确起点。来源内容、提示词、回复、终端正文、
原生凭证和磁盘路径不会加入遥测 payload。没有稳定来源的记录不通过邻近时间猜关联。

SQLite WAL/FULL，ACK 前不删内容；ACK 后保留去重 tombstone。源事件与 outbox 之间的崩溃窗口
由事件重放修复；未走 factory 的持久事件也由 sync 补采。每个 origin 独立流内序号，
更换 deployment 后旧 payload 保留原样，需 Controller 显式授权有限历史代传。

原生 Session 的 turn 完成表示 idle，不误判永久完成。生命周期版本来自来源发生时间，
延迟补采保留旧版本；同版本冲突交给服务端隔离。Project 配置 A→B→A 可产生新的有效快照。
队列满时追加失败可观测，但同步仍先清空已排队批次，避免永远无法恢复。

当前边界：全入口调用前暂停/预算门、source cursor 快照对账、旧格式/未接入 Provider 追补、
自动 tombstone 留存与隔离修复、实例租约/密钥轮换仍未完成。已结算 Terminal 不等于任意 shell
直连调用都可观测；未知 usage 和实际账单金额不能当零。

## 验证方式

`tests/test_controller_observation.py` 验证真实 CLI、custom state、单机兼容、EventWriter、
原生 Session、终端持久回执、补采/重启、ACK 事务、容量恢复、来源代传及 JSON Schema。
schema fixture 来自 Controller 的 `protocol-schema`，生产代码不跨仓库导入。

核心变更按 EventLog/Config/直接 caller 运行聚焦回归，并执行 dev-premerge-gate 与三流 mock E2E。
dev-verify 当前因公共 config 导入图扩展到 1017 个测试文件；本次执行 plan/run，明确排除自动
全量 deterministic tier，改由聚焦闭包单独运行；保留 serial/contract/mock_e2e tiers。
不把文件数触发的全量计划作为必要回归范围，也不声称执行了全量 pytest。

跨仓库 Keycloak/PostgreSQL/Docker Playwright 使用
`scripts/check-controller-observation-producer.py` 产生两次原生 Session turn，重启同步后总计仍为
240 tokens；验证登录、项目/会话历史、下钻、管理员汇总及用户隔离。没有真实模型调用或账单。
