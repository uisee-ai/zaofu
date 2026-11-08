# Worker 释放后的迟到用量统计

关联：[199 恢复与角色交接](../design/199-worker-failure-recovery-and-role-handoff.md)、[实施任务](../../tasks/2026-11-08-1349-usage-idle-liveness.md)。

LS-v1 的 FMS17、FMS03 已被内核释放为 idle，但磁盘用量采集稍后发布带旧 task_id 的 agent.usage。原 usage_liveness 只在 usage 没有显式 task_id 时保留 idle/awaiting_review，导致记录又变为 busy；pane 仍保留完成文本，超过心跳阈值便产生假卡顿告警。任务仍未完成或等待后继并不说明该 worker 还在实施。

usage_liveness 现在始终读取已有角色状态。同任务或无任务的 usage 保留 idle/awaiting_review，计费与活动时间仍正常记录；真实 task.dispatched 或 worker.state.changed 已种下 busy 后，usage 仍正常刷新 busy。原 fanout 终态和显式其他任务的处理保持原行为，本变更不赋予 usage 派发或完成权限。

`tests/test_usage_terminal_liveness.py` 经过真实 Orchestrator housekeeping 验证 event/payload 两种 task_id、idle/awaiting_review、费用记录及 pane 告警结果；另验证同任务重派和新任务派发不会被旧 idle 压住。历史已污染的心跳不通过直接编辑 role_sessions.yaml 清除，部署后必须单独核查运行状态，不能将修复未来事件等同于清理历史投影。
