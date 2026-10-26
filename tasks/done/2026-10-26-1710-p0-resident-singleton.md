# P0 Autoresearch resident 单实例与 stale cleanup

> 状态: done

## 问题
同一 state_dir 同时存在多个 autoresearch resident，重复消费事件并争抢 recovery/repair 资源。

## 范围
- start 使用 state-dir scoped lock，已有健康 resident 时拒绝第二个实例。
- stale pid 可恢复，异常退出自动释放锁。
- stop 清理 lock/pid，并保留可审计事件。

## 验收
- step -> verify: 同一 state_dir 并发 start 只能成功一个 resident。
- step -> verify: stale pid/死进程不阻止下一次启动。
- step -> verify: stop 后可以重新启动，现有 resident sidecar tests 全部通过。

## 完成记录
- 实现提交: `fb04e29 fix: close P0-P1 runtime recovery gaps`
- 验证: 聚焦回归 122 passed；resident sidecar 6 passed；全量确定性层 11632 passed、32 failed、19 skipped，失败为工作区既有 fixture/config/阈值问题。
