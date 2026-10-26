# P0 Worker stuck 与 lease recovery 闭环

> 状态: active

## 问题
Worker stuck/lease_expired 后会产生 attention 或 shadow-only failure，但 respawn briefing 注入失败时没有可执行的同任务恢复路径，最终没有新的 redrive。

## 范围
- 让 Task Pipeline recovery 为 respawn failure 保留 task/operation/attempt 关联。
- 对可重试的 worker lifecycle failure 产生唯一、幂等的 same-operation redrive 请求。
- 失败超过上限时进入明确 blocked/attention 状态，而不是静默停滞。

## 验收
- step -> verify: 模拟 lease_expired + respawn injection failure，验证同 operation 只生成一个 redrive。
- step -> verify: 重放 recovery tick 不重复派发 attempt。
- step -> verify: 达到 retry cap 后输出 blocked reason 和 owner。
