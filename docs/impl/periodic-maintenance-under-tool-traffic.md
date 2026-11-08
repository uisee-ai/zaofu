# 连续工具事件下的周期维护

对应[199 的持续执行与恢复责任](../design/199-worker-failure-recovery-and-role-handoff.md)。

`zf start` 的周期 tick 经 `_run_orchestrator_idle_tick` 调用 `Orchestrator.run_once()`，负责 durable catch-up 与既有恢复检查。`consume_durable_nonwake_hooks` 为纯 Codex allow/post-tool Hook 批次提供轻处理：只更新 liveness 和消费游标，避免每条高频 Hook 触发全局扫描。然而连续纯 Hook 批次会使每个周期都提前返回，恢复职责可能一直得不到执行。

轻处理现在最多连续推迟一个维护周期。下一次周期遇到相同模式时，仍先消费精确 Hook 边界，再执行原有维护路径。普通控制事件或无待消费事件会正常维护并清除推迟标记。标记只是当前进程的调度提示，不是持久化业务状态；重启后最多再次推迟一次，不会跳过 durable 事实。

该改动没有新增定时器、角色或第二套调度器，不将每个 Hook 升为唤醒事件，也不绕过暂停、快照一致性、任务身份或 provider liveness 检查。它保证周期维护获得执行机会，不保证每个检查都必然派发任务，也不承诺固定墙钟时限；检查耗时、输入依赖和其他门禁仍需单独分析。

测试经实际 CLI 周期入口覆盖持续 Hook、一次轻处理、回调追加事件的游标边界，以及正常控制/deny事件。ls-v1 的 Reader 排队同时观察到快照并发失效；这是另外一条待核实的时延因素，本修复不放宽该一致性保护，也不能当作全部现场排队根因已经消除。
