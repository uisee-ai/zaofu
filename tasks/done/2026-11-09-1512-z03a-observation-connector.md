# Z03a 持久观测 Connector 与实际来源接线

> 状态: done

来源：用户要求继续实施 203 tokens、Dashboard、Project/Session；本项是 Z01/Z03 的观测切片，父任务继续保留完整 managed 义务。

## 验收

1. 保留单机，接入真实命令 -> verify: 默认无 Controller 状态/网络依赖；custom project.state_dir、配置校验、zf controller status/sync 测试通过。
2. 捕获实际事件与已结算 Terminal -> verify: EventWriter、原生 Session 完整 turn、短会话历史、成本规范化、缺失、取消、无正文外传及同版 JSON Schema。
3. 断点和容量恢复 -> verify: 原事件先持久化、队列失败可重放、ACK 身份事务、重启去重、origin/mode 历史、独立流序号及满队列仍可上传。
4. 真实服务联验 -> verify: 独立安装的 ZaoFu CLI 经真实 Keycloak 上传至 Controller/PostgreSQL，Docker 浏览器历史与汇总正确。
5. 共享边界回归 -> verify: EventLog/Config/直接 caller 聚焦测试、dev-premerge-gate、三流 mock E2E。

设计与运行边界：[202](../../docs/impl/202-controller-observability.md)。实现 commit 与准确测试层级在归档时记录。

未包含：全入口 managed 认证/预算/限额、命令执行、完整快照对账、未接入来源补齐和真实 Provider 计费资格；仍归 Z01–Z04。

## 完成记录

实现：`2d0ead6d feat: report project session and token usage through a durable controller connector`。

- 新增 Connector 定向测试：24 passed；EventLog/Config/成本/CLI 聚焦闭包：325 passed；追加 Session/终端实际 caller 闭包：136 passed（集合存在重叠，不累加为唯一测试数）。
- dev-verify plan/run：公共 config 引用扩展到全量，明确排除 deterministic 自动全量层；单独执行上述聚焦闭包。serial 10、sentinel 53、三流 mock E2E 10 均通过。
- CLI 生成手册经 manual-docs.py generate 更新，currentness 通过；ruff 与 diff 检查通过。
- 实际 Keycloak + Controller PostgreSQL + Docker Browser 联验通过；未使用真实模型账户。
- 原生 Codex 本机真实用量验证及合入 dev 是用户新增的后续要求；继续执行，不将其写成已完成。
