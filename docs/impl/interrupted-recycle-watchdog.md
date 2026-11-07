# 控制面重启后的中断回收接管

控制面可能在 context recycle 的 terminate/spawn/briefing 交付之间退出。
worker.state.changed 保留 recycling，但新 watcher 的本地 `_instance_state`
为空。旧 provider heartbeat 尚新不能证明新 pane 已启动完成，尤其是只剩
shell、没有 provider 子进程时。

`_capture_logs` 保留原 dead probe 次数、safe halt、blocked human 和活动归属
检查。当持久化状态为 recycling、本 runtime 尚无该 role 的本地回收 marker，
且仍有当前 Task 或 fanout child 时，允许即时进入既有 `_respawn_instance`。
使用 `interrupted_context_recycle` 原因、`force_fresh_session=True` 和
`inject_idle_prompt=False`，避免恢复耗尽的旧聊天上下文或复活无任务空闲角色。

实际恢复仍经过 role 级 SessionLock、停止确认、SpawnCoordinator、ready 检查
和原 Task/fanout recovery briefing。锁冲突返回 respawn_in_progress，随后可
再次观察；不另造 Task、operation、attempt 或 generation，不改原 workdir。

本地 recycling marker 只排除本次新增的即时接管，不被解释成无限期在途锁。
原 `_start_recycle` 和 `_capture_logs` 在同步 runtime 链路执行；本地回收失败
会保留 marker，但原 stale-liveness recovery 必须继续可用。跨进程 operator
restart 的互斥仍由已有 respawn SessionLock 提供。

该补丁不承诺 actuator 优雅排空全部替换，也不新增 launcher Enter 补发。
仅补 Enter 无法恢复已丢失的旧 watcher 后续 briefing 调用栈；受控恢复会
完整重建并交付。本次未修改 native compact、context 阈值或 hard-cap 计时。

验证及明确延后事项见
[执行任务](../../tasks/active/2026-11-07-0352-interrupted-recycle-watchdog.md)。
