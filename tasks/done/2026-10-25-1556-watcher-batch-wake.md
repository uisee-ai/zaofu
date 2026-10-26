# 合并同批唤醒，避免并行交接排队

> 状态: done — `77d00191 fix: coalesce watcher snapshots into one runtime wake`；合入 dev `1061ca74 fix: eliminate per-event runtime wake backlog`。

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
- 上线及真实并行证明见下；此任务完成不代表loshu-fms整项目验收完成。

## 真实运行验收

- 16:07:36 UTC仅重载watcher，3334379→3491709；8个既有worker PID/start ticks核对保持。source worktree已合并、检查无活跃使用者后清理，未push；未修改既有docs/design/00-index.md。
- 正式Planner→Critic通过（evt-42735270c430），新Preview evt-38f5aafb3673的完整工件摘要校验通过；受控Owner授权批准evt-c9cae2a671d7。
- 16:12:19新代际tpg-4858ea5645f15b73663f接纳37项；原Scaffold/Environment排除。两个旧Task合同逐值保持、旧代际tpg-b7340f8e5cc26738ed9e保持。
- 16:14:32 Inventory operation started；16:14:59实际同时Impl=2：Environment/dev-lane-0与Inventory/dev-lane-1；不同operation、dispatch和worktree。Environment dispatch tpd-f263cd5-53d7d91c6ef8-p3保持。
- Inventory已真实读取impl-g1-p1 briefing，基于fca394f6候选Scaffold；Environment同时运行并生成真实runtime交接。完整证据目录 `/home/min/workspace/public/zaofu-evidence/early-parallel-20261025/`，文件parallel-proof.json、provider-parallel-observation.json、new-plan-review.json、plan-approval-receipt.json、wake-merge.json。
- 边界：本轮仅证明2项早期并行，未证明8lane满载或Verify并行；新增slice尚未完成实现/验收。Environment普通runtime通过，但生命周期入口/部分合同项及祖先FD宿主资格未完成，原依赖门保持。
- 性能边界：修复同批重复完整协调；本次启动补读/全局协调仍耗数分钟（批准16:10:24→新任务开始16:14:32），不声称所有性能瓶颈归零。后续若稳定运行仍持续积压，应单独采样全局扫描开销；本轮py-spy只读attach被宿主权限拒绝，没有提权或改ptrace配置。
