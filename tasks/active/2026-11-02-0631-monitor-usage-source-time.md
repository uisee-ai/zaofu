# 监控区分用量采样时间与账本记录时间

> 状态: active

## 证据

LS 控制面重启后重新观察历史 provider 用量，`agent.usage evt-1a62516861e0` 写入时间为 2026-11-02T06:29:30Z，但 `usage_timestamp` 为 2026-11-01T08:37:46Z。该事实合法用于账目，不能将新入账视为当时的 worker 活动。历史 `task.attempt.heartbeat evt-0fbe81fde318` 通过 `source_event_id` 指向同类旧用量。

## 验收

1. 对直报 usage 和引用 usage 的 attempt 心跳投影采样时间及来源 -> verify: 分别保留原事件 ts 和来源 usage_timestamp，未找到同任务来源不伪造时间。
2. 监工提示要求区分来源时间、账本时间和真实进展 -> verify: 无来源时保持未知，worker 心跳或用量不冒充结果验收。
3. 测试并部署 -> verify: Run Manager、resident、事件调用方和 mock 流程定向测试通过；LS 的旧用量显示原采样时间。
