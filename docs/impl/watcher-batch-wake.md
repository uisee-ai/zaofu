# Watcher 按读取批次唤醒

2026-10-25，loshu-fms 真实运行中，约 53 MB 事件账本上的一次完整协调约 40 秒。
原 `zf start` 在逐事件回调中为每条 wake 运行一次协调，再补处理同批观察事件，
导致 Planner 完成到 Critic 接手出现数分钟积压。

`src/zf/cli/watched_batch.py::process_watched_batch` 接收 EventWatcher 的不可变读取批次。
`zf start` 通过 `watch_batch.py::make_watch_callbacks` 接线，共用这一批次处理入口。
逐条检查原 wake predicate / rate limiter；任一唤醒时，所有事件保持发生顺序，
只调用一次 `run_once(events=batch, consumed_offset=snapshot_end)`。
没有唤醒的观察批次继续由 idle durable catch-up 处理；不提前确认其游标。
反应追加的事件位于 snapshot_end 之后，留给下一轮或重启补读。

2026-10-31 一致性修复：带 `consumed_offset` 的运行时调用从 Session 已确认位置
读到 watcher 的不可变上界，补齐前一观察批次；idle 已消费的事件不会再次应用。
`EventOffset` 保持整数 byte-offset 兼容，同时携带 device/inode。正常读取只读活动
文件尾部；身份变化时定位归档中的原 segment，读完未消费尾部，再读后续 segment。
SessionStore 原子保存 `latest_event_offset` 与可选的 `latest_event_identity`；
watcher 即使遇到新旧文件长度相同，也会交付新事件。捕获字节时复用 writer 锁，
解码和业务反应在释放锁后进行，避免反应追加越过批次上界。

旧 Session 缺少身份字段时保持 active-file 数字 offset 的兼容语义，首次成功读取后
保存身份；升级不会自动重放无法定位的旧历史事件。保留归档是跨轮转补读的前提。
其他自行把 offset 转为普通整数的持久化消费者也需要显式保留身份，才能获得相同保障。

模拟终态仍先由 Kernel 结算 Task / 看板，成功后才写 simulation.done 并停止 watcher。
不修改事件类型、业务状态所有权、任务依赖或并发配额；整轮协调自身的开销仍然存在。

验证：`tests/test_watched_batch.py` 覆盖同批合并、观察/限流、失败不停止和追加边界；
`tests/test_cli_start.py` 覆盖真实 CLI 接线；
`tests/e2e/test_terminal_kanban_convergence_mock_e2e.py` 覆盖真实 Orchestrator / Store / Kanban
结算与重启去重。任务记录见 `tasks/done/2026-10-25-1556-watcher-batch-wake.md`。

两侧事件批处理的集成与验证记录见 [2026-10-26 dev 同步](../records/2026-10-26-0938-upstream-dev-integration.md)。

本次回归与任务归档语义见 [事件与任务当前态修复](review-state-consistency.md)。
