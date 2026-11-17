# Z05 云端 Runtime 交付与真实实例联验

> 状态: active

来源：Owner 授权继续实施 203/204、评估并纠正不合理方案。审核基线 `bbefdff0`，
联动 Controller Q01b；保留 standalone 与 managed 共用 Kernel/Store/Event 合同。

## 验收

1. 可交付镜像 -> verify: 从干净 checkout 构建，包含当前完整 Web、CLI、项目模板、
   skills 与必要宿主工具；非 root、只读根文件系统、独立持久 HOME/项目可启动。
2. 实际云端入口 -> verify: `zf controller serve` 消费真实身份和 WorkspaceConfig，
   经真实 Controller/Gateway 完成空工作台、项目导入及受管功能；无身份访问拒绝。
3. 生命周期与观测 -> verify: 持久卷重建、身份代次变化、Project/Session/用量上报和
   断连恢复保持合同；终端/Workflow 不另建绕过准入的执行入口。
4. 评估和纠正 -> verify: 每项反例有聚焦回归、修正 spec 与实际联验证据；未知
   Provider/生产资格留明示缺口，不能以构建成功替代完整产品验收。

使用独立 worktree，保护主目录其他 session 的文档修改；按影响闭包执行 dev-verify。
不把 Controller 的数据库或集群凭据放进租户镜像，不改项目 canonical state。

实施与纠正 spec：[208 云端 Runtime 交付](../../docs/impl/208-cloud-runtime-delivery.md)。
已完成镜像与空工作台接线验证；新增项目准入/ACK/初始化重试以及云端历史 Outbox 交接。
聚焦回归已通过，真实 Gateway 与重建复验继续进行；完整 Session/Provider 等仍未关闭，任务保持 active。


追加修正：镜像内固定 Herdr/Codex 工具、Herdr 冷启动限时重试、待初始化 Project
跨代次显式重新准入。聚焦闭包 131 项通过；真实沙箱终端画面、Keycloak 撤销 WS、换代重试与新 Session 停止投影已复验。

终态验收追加发现并纠正 execution 收据覆盖终端 stopped 的投影冲突；实际 caller 红绿
回归、131 项相关闭包与 53 项 sentinel 通过。真实 CLI 未输入模型凭据或推理提示；
Controller 看到停止 Session 及 pending/null 用量，完整模型调用资格保持未完成。
