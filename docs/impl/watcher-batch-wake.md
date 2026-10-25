# Watcher 按读取批次唤醒

2026-10-25，loshu-fms 真实运行中，约 53 MB 事件账本上的一次完整协调约 40 秒。
原 `zf start` 在逐事件回调中为每条 wake 运行一次协调，再补处理同批观察事件，
导致 Planner 完成到 Critic 接手出现数分钟积压。

`src/zf/cli/watched_batch.py::process_watched_batch` 接收 EventWatcher 的不可变读取批次。
逐条检查原 wake predicate / rate limiter；任一唤醒时，所有事件保持发生顺序，
只调用一次 `run_once(events=batch, consumed_offset=snapshot_end)`。
没有唤醒的观察批次继续由 idle durable catch-up 处理；不提前确认其游标。
反应追加的事件位于 snapshot_end 之后，留给下一轮或重启补读。

模拟终态仍先由 Kernel 结算 Task / 看板，成功后才写 simulation.done 并停止 watcher。
不修改事件类型、业务状态所有权、任务依赖或并发配额；整轮协调自身的开销仍然存在。

验证：`tests/test_watched_batch.py` 覆盖同批合并、观察/限流、失败不停止和追加边界；
`tests/test_cli_start.py` 覆盖真实 CLI 接线；
`tests/e2e/test_terminal_kanban_convergence_mock_e2e.py` 覆盖真实 Orchestrator / Store / Kanban
结算与重启去重。任务记录见 `tasks/active/2026-10-25-1556-watcher-batch-wake.md`。
