# Synth 基础设施失败的精确断点恢复

提供方额度耗尽或会话终止，可能使 Kernel 在收到正式审查结果前记录
`worker_noop_or_terminal_missing`，同时将 fanout aggregate 置为 failed。
普通角色重启不会重新打开这个已终止载体；阶段 replan 也不能代替恢复已有读取。

操作者确认外部故障恢复后，可以执行：

```sh
zf restart plan-critic --resume-failed-synth <fanout.synth.completed 的失败事件 ID>
```

CLI 沿用 run-contract 检查与角色 respawn 互斥锁。只有当前运行中 Run、
同一 operation/request/attempt、精确 Kernel dispatch-loss 因果链、没有正式结果、
没有活动提供方回合的机械 synth 失败可恢复。预算阻塞、提供方策略拒绝、
语义否决、已准入结果、过期候选及损坏请求均不能经此路径继续。

恢复通过 EventWriter 追加 aggregate started 和 synth dispatched，然后复用
原 briefing 的正常角色恢复派发。原 request、预算、source manifest、读取账本
和历史失败事件保持原义；不生成通过结论。两次追加之间中断可再次使用同一
失败 ID 补齐，完整派发后同一失败 ID 不会再次打开载体。

入口和回归：`src/zf/cli/restart.py`、`tests/test_synth_restart_checkpoint.py`。
运行中 reader 的重启保护见 [reader-respawn-continuation.md](reader-respawn-continuation.md)。
