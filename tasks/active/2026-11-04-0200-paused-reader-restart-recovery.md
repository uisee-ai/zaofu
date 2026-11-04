# 暂停 Run 重启时保护 reader 检查点

> 状态: active

## 真实复现

隔离 PRD 项目 `/tmp/zf-plan-real-20261104-0145`：Planner 执行时先 `run.paused` 并正常 stop；新控制面启动时保留 paused。恢复预览原本给出 `redrive_interrupted_reader_operation`，但 watcher 在 Run 恢复前针对同一 launch 信号重复写 `fanout.child.dispatch_lost`，随后错误记录 `fanout.child.failed`、`workflow.operation.failed` 和 `prd.plan.failed`，把原可恢复检查点破坏，尝试 replan。证据 `evt-0b3e921fb76b`、`evt-12449cd2194c`、`evt-9501a9ee2635`、`evt-b2d14bcba442`。

## 验收

1. suspended reader child/synth 的 Run 暂停，启动 liveness 恢复扫描两次 -> verify: 无 loss 预算消耗、终态、replan 或重复派发，原 request/attempt 保持；其他活跃 Run 不受影响。
2. 模拟判定与派发间 paused race / 其他可重试拒绝 -> verify: 同一来源信号与未送达动作不造成恢复预算耗尽；恢复后一次合法续派，保留 request hash、工作目录、provider session 绑定。
3. 未暂停且真实新 provider 丢失 -> verify: 仍受有界恢复和正确终止机制保护，其他 Run/其他 signal 不被错误合并。
4. 对受控暂停、停止、重启、预览、恢复流程做模拟和真实 Provider 演练 -> verify: 既有 scan 不重复；相同 Planner operation 不失败，最终可继续至 Critic；真实与模拟分别记录。
5. 运行 reader/synth、EventLog、Run admission、recovery、caller 闭包及 sentinel/mock E2E -> verify: 全通过，记录真实 Provider 的适用范围与残余风险。

## 补充发现：暂停期间超时扫描

暂停 Run 后将时钟推进至超时阈值：原 `_check_fanout_timeouts` 仍写入
`fanout.child.failed` / `fanout.timed_out`；长暂停后 `run.resumed` 紧接着扫描
也可能沿用暂停前计时立即失败。为暂停状态跳过扫描，恢复后以最近恢复时间
提供新的有界窗口；child、synth 和其他 Run 分别验证。该策略重置窗口，不是
精确扣除已消耗预算，语义和界限应在文档中说明。
