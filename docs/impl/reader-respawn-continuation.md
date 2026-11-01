# Reader 角色重启的原派发继续

`zf restart <role>` 已经通过角色替换锁串行化 provider 的停止、重建和恢复简报投递。
Reader/Synth 的失联恢复扫描也必须尊重这一边界，否则新进程启动记录会在恢复简报
投递前被识别为失联，或者投递成功后仍消耗一次失联重试，最终错误关闭审查载体。

`reader_respawn_continuation.py` 提供两个机械检查，接入 Child 与 Synth 共用的
`_reader_dispatch_lost_event_after`：

- 已有角色替换锁实际被持有时，暂不据中间启动记录判定失联。只检查锁，不创建或删除锁；
  陈旧的未持有锁文件不会阻止恢复。
- `worker.recovery.injected` 绑定原派发事件 ID、fanout/run、角色以及存在时的
  Operation/hash。成功投递只消除它之前的失联信号；投递后的 provider 终止仍触发
  原来的有界恢复规则，错误身份或其他派发的投递也不构成继续证据。

恢复投递沿用原简报、原 Operation 和原 dispatch，不生成新语义结果，不改写不可变
请求、读取要求、预算历史或已拒绝的结果。恢复提示提供当前部署的 CLI 路径，避免原
简报中的旧可执行文件在恢复后误读新版配置。CLI 的角色恢复入口通过测试验证；mock
测试覆盖重启持锁期间的并发扫描、投递完成后尚未产出新文本，以及错误身份和后续失败。

部署时可先停止 watcher 并使用 `zf start --control-plane-only` 保留已有 provider；
不得把控制面更新解释为取消或通过仍在运行的审查。具体模型参数调整仍通过 `zf.yaml`
以及正常角色重启生效，并保留 provider 报告的真实上下文容量。
