# 用量统计保留已释放 worker 的状态

> 状态: active

授权：持续监工、遇到问题修复并记录。LS-v1 的 FMS17、FMS03 已正式 blocked，内核释放角色为 idle；随后带旧 task_id 的磁盘用量统计又将心跳投影改为 busy，600 秒后触发 pane activity mismatch。只修复用量事实对已释放角色状态的错误覆盖，不修改任务、operation 或派发门禁。

- 终态释放后收到相同任务的显式 usage -> verify: 真实 Orchestrator housekeeping 保留 idle/awaiting_review，费用仍入账，pane 不产生卡顿误报。
- 新任务或同任务真实重派 -> verify: task.dispatched 种子后的 usage 仍刷新 busy，真实忙碌检测继续有效。
- 无 task_id 的现有 usage 和 fanout 终态 -> verify: 原心跳、fanout 与 stuck 恢复回归通过。
- 合并部署 -> verify: 保留在途操作与 provider 会话；只读现场历史回放证明两条迟到 usage 不会把 idle 变 busy；旧已污染投影明确单独观察。

证据：evt-1cb69af98f00 idle → evt-096f74e28f7d usage；evt-67c58e8f655d 为另一旧任务 usage。根因是 usage_liveness 仅在缺 explicit_task_id 时读取/保留已有 idle，不能将 provider 计费当作重新派发。

实现：[用量迟到说明](../docs/impl/usage-after-worker-release.md)。4项入口回归先RED，修复后6文件110 PASS；dev-verify解释影响后按187文件闭包运行2761 PASS（规划器因文件数建议全量，遵守AGENTS按影响闭包执行）；serial10、哨兵53、design199/redrive mock3通过，未跑完整pytest或真实provider E2E。现场两条真实usage分别在隔离临时registry重放，baseline busy、fixed idle，不修改live状态。
