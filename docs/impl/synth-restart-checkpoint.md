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

同一请求的一次传输续接完成派发后，若提供方再次关闭且没有提交结果，Kernel 以第二个 fanout 失败事件结算该 WRC。重复处理第一次或第二次失败均不会多记一次。传输续接的派发记录在 fanout 与 WRC 的结算间隙也有效，CLI 拒绝再次打开同一请求。此时需要诊断缺失终稿的原因，原 Planner 产物和预算仍绑定原请求。

升级后重开旧断点时，恢复提示仍按原派发区分 synth 与 reader。旧角色索引同时列出来源和安装目录时，运行时核对当前角色、后端、索引中的原始路径及安装文件摘要，再给出工作进程可读的 `SKILL.md` 绝对路径。原始 briefing 和必读回执继续绑定同一请求。

入口和回归：`src/zf/cli/restart.py`、`tests/test_synth_restart_checkpoint.py`。
运行中 reader 的重启保护见 [reader-respawn-continuation.md](reader-respawn-continuation.md)。

若独立 Critic 的传输续接已耗尽，之后获准的固定输入 Planner 诊断会核对同一 fanout 的提供方回合关闭、非语义派发丢失、终止事件和 WRC 失败结算。内核通过原 WRC 请求与触发事件还原 Planner 代际，继续核验冻结的 Candidate、任务图、OA 范围和本次受控恢复回执。新 Planner 接收本次提交的 Owner 反馈，原 Critic 的失败和重试计数继续保留。
