# 已留待阶段交接的任务不重复报告 worker node-skip

> 状态: done · a961d26e fix: avoid stale drift alerts for terminal assignments

LS 的 `LS-DESIGN-TEMPLATES-R2` 保留 `dev.blocked` 交接证据，启动时产生
`task.requeue.skipped`，当前无 active_dispatch_id；此后 `dev-lane-0` 的
低级 node_skip 约每分钟重复，41 分钟 54 次。该信号仅 observe，但增加噪声。

1. 对账 Task Pipeline 的当前 dispatch 和 operation 终态 -> verify: 已结束的
   provider assignment 不要求角色仍有活跃 turn，既有阶段事实不被清除。
2. 新 dispatch、无交接证明、错误任务/角色或无效事件 -> verify: 真实缺席仍能
   发出 drift；不修改任务/Run 状态，不屏蔽其他 signal 或绕过恢复门禁。
3. mock 漂移检测及调用方测试、event contract/sentinel、mock flow -> verify:
   回归通过；在途 LS 不因修复重启 provider，部署后核对实际告警变化。

## 现场核对与验证

LS 的 operation `wop-impl-tp-LS-DESIGN-TEMPLATES-R2-5aed2a-811c528b4361`
已 settled，dispatch / role / task map generation 与上次派发一致；任务仍待后续
交接。修复前终态组合 10 例复现误报，修复后调用方闭包 92 项通过；新 pipeline、
legacy 派发、同角色其他任务及证据不可用仍报警。已合入 dev d5ba7dd8；2026-11-04 08:05 UTC 控制面重载产生 loop.started evt-5adc4f87730e，原 Critic 进程保持运行。此后至 08:08 UTC 未再产生该误报，Web HTTP 200；在途任务未被修改。
