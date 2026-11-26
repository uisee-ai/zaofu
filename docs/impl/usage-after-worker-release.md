# Worker 释放后的迟到用量统计

关联：[199 恢复与角色交接](../design/199-worker-failure-recovery-and-role-handoff.md)、[实施任务](../../tasks/2026-11-08-1349-usage-idle-liveness.md)。

LS-v1 的 FMS17、FMS03 已被内核释放为 idle，但磁盘用量采集稍后发布带旧 task_id 的 agent.usage。原 usage_liveness 只在 usage 没有显式 task_id 时保留 idle/awaiting_review，导致记录又变为 busy；pane 仍保留完成文本，超过心跳阈值便产生假卡顿告警。任务仍未完成或等待后继并不说明该 worker 还在实施。

usage_liveness 现在始终读取已有角色状态。同任务或无任务的 usage 保留 idle/awaiting_review，计费与活动时间仍正常记录；真实 task.dispatched 或 worker.state.changed 已种下 busy 后，usage 仍正常刷新 busy。原 fanout 终态和显式其他任务的处理保持原行为，本变更不赋予 usage 派发或完成权限。

`tests/test_usage_terminal_liveness.py` 经过真实 Orchestrator housekeeping 验证 event/payload 两种 task_id、idle/awaiting_review、费用记录及 pane 告警结果；另验证同任务重派和新任务派发不会被旧 idle 压住。历史已污染的心跳不通过直接编辑 role_sessions.yaml 清除，部署后必须单独核查运行状态，不能将修复未来事件等同于清理历史投影。

## 返工替换会话后的磁盘用量

2026-11-26 的真实 8×8 中，tech-scan 第一次投递中断，TaskAttempt 停在 `delivering`。第二个 attempt 在新 Codex 会话中完成后，磁盘采集又读取了同一新会话的最后两个样本。按角色筛选时，第一轮遗留的 attempt 恰好是唯一仍处于活动状态的行，因而收到错误的续租。

Codex 用量回执现在保留 transcript 中的原生 `provider_session_id`。按派发隔离的 Reader 将该会话与 RoleSessionRegistry 登记的 Run、task、dispatch 及已发送的 TaskAttempt 对账。复用会话的角色还需要正在运行的 WRC operation 明确绑定该会话，或由当前任务阶段的角色、阶段和放置代次证明归属。样本早于派发、目标已结束、投递只有 claim，或会话身份无法对账时，系统保留用量，记录 `usage_binding_status=unattributed`，不给待执行任务或角色租约续期。旧版无原生会话标识的兼容采样仍支持唯一的已发送 attempt。

回归入口是 `tests/test_disk_usage_attempt_attribution.py`，覆盖旧投递悬挂、新会话真实派发、迟到采样、不同会话和未知归属的费用保留。

读取 Codex 记录时，已换行的损坏 JSON、非对象记录或用量事件类型不符使该次用量记为缺失。正在写入的末行尚未封口时，费用采集可以沿用之前完整的计数，原生区间等待完整采样后再证明。
