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
已完成镜像与空工作台接线验证；项目首次初始化出现真实准入循环，继续实施，不关闭任务。
