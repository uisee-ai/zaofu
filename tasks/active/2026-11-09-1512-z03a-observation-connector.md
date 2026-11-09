# Z03a 持久观测 Connector 与实际来源接线

> 状态: active

来源：用户要求继续实施 203 tokens、Dashboard、Project/Session；本项是 Z01/Z03 的观测切片，父任务继续保留完整 managed 义务。

## 验收

1. 保留单机，接入真实命令 -> verify: 默认无 Controller 状态/网络依赖；custom project.state_dir、配置校验、zf controller status/sync 测试通过。
2. 捕获实际事件与已结算 Terminal -> verify: EventWriter、原生 Session 完整 turn、短会话历史、成本规范化、缺失、取消、无正文外传及同版 JSON Schema。
3. 断点和容量恢复 -> verify: 原事件先持久化、队列失败可重放、ACK 身份事务、重启去重、origin/mode 历史、独立流序号及满队列仍可上传。
4. 真实服务联验 -> verify: 独立安装的 ZaoFu CLI 经真实 Keycloak 上传至 Controller/PostgreSQL，Docker 浏览器历史与汇总正确。
5. 共享边界回归 -> verify: EventLog/Config/直接 caller 聚焦测试、dev-premerge-gate、三流 mock E2E。

设计与运行边界：[202](../../docs/impl/202-controller-observability.md)。实现 commit 与准确测试层级在归档时记录。

未包含：全入口 managed 认证/预算/限额、命令执行、完整快照对账、未接入来源补齐和真实 Provider 计费资格；仍归 Z01–Z04。
