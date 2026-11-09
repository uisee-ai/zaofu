# Delivery 备用卡片的运行恢复状态

当项目没有 Feature 列表或 Feature 索引时，Web 的 Delivery 卡片从 Task 和
EventLog 引用发现候选目标。`summaries._delivery_runtime_statuses` 只读地按事件
顺序投影这些目标，不生成 Feature，也不恢复 Run 或修改任务状态。

`fanout.aggregate.started` 表示聚合已启动或由受控恢复再次启动，应替换同一
Fanout 的旧 cancelled/blocked 显示。`run.goal.updated` 仅在明确携带
`status=active|running` 时恢复同一 Trace/Run 目标的 blocked 显示；普通元数据
更新不代表恢复，已完成目标也不能被这个更新重新打开。其他目标不受影响，
后续新的终态仍按顺序覆盖。

`fanout.aggregate.completed` 是一次聚合结算事实，不保证成功。其 payload 的
failed/timed_out 显示 blocked，cancelled 保留 cancelled，completed 显示 done；
缺省 status 沿用现有 completed 语义。

这条投影由 `/api/state`、Snapshot 和项目 Board 的 features/delivery_features
共同使用。`tests/test_web_delivery_runtime_recovery.py` 通过实际 API 验证事件追加
后的缓存刷新、目标隔离、恢复后再次阻塞、失败结算以及只读性。当前变更不修改
任何终态历史、预算授权、Critic 准入或 Kernel 状态转换。
