# 合并同批唤醒，避免并行交接排队

> 状态: active

用户授权继续执行，以能够并行为目标；监工在 loshu-fms 重规划中复现当前 dev 的逐事件唤醒积压：Planner完成事件已落账，worker heartbeat/hook/state等同批事件各触发一遍完整协调，单次约40秒，15:51记录的trigger lag已552秒。事件日志约53MB，控制面CPU约97%。证据：`/home/min/workspace/public/zaofu-evidence/early-parallel-20261025/`，实际结果 `evt-a8c4dd82f551` 等待Critic派发。

- 合并同批唤醒 -> verify: 多条wake和观察事件按原顺序仅一次run_once，所有事实仍处理，原snapshot offset在成功后确认。
- 保留纯观察补读/重启 -> verify: 无wake不提前ack，idle仍从durable offset补读，回调追加事件留下一批。
- 保留模拟结束 -> verify: terminal先推动canonical Task/看板收敛，才emit simulation.done/stop；重启不重复终态。
- 实际接线 -> verify: zf start的EventWatcher使用该批处理入口，mock E2E覆盖消费者。
- 验证及上线 -> verify: 聚焦影响闭包、premerge、mock E2E；合dev，仅重启控制面保留worker身份；继续正式Plan审批并检查真实两任务重叠。

不改变配置、任务依赖、验收门或事件账本；不删除事实以减少唤醒。

实现说明：[Watcher 按读取批次唤醒](../../docs/impl/watcher-batch-wake.md)。

## 实现与验证

- 原实现 RED：同一批4条事实触发4次协调；新实现仅一次，保持顺序和snapshot边界。
- 聚焦主回归66 passed；调用边界82 passed；premerge 51 passed；含终态看板/恢复mock E2E和真实CLI接线。
- dev-verify plan/run已记录：CLI main的import范围扩为119文件，工具按80文件阈值建议全库。遵循AGENTS按影响闭包验证及避免小改全库规则，显式跳过自动全库/serial层，以以上聚焦运行替代。receipt的passed只表示规划执行成功，不能冒充全量通过。
- 尚待上线控制面重载与真实并行证明。实现提交后记录commit；本任务此刻仍active。
