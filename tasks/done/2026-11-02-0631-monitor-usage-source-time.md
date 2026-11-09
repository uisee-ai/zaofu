# 监控区分用量采样时间与账本记录时间

> 状态: done — `792cf5e9 fix: expose source time for monitored usage`

## 证据

LS 控制面重启后重新观察历史 provider 用量，`agent.usage evt-1a62516861e0` 写入时间为 2026-11-02T06:29:30Z，但 `usage_timestamp` 为 2026-11-01T08:37:46Z。该事实合法用于账目，不能将新入账视为当时的 worker 活动。历史 `task.attempt.heartbeat evt-0fbe81fde318` 通过 `source_event_id` 指向同类旧用量。

## 验收

1. 对直报 usage 和引用 usage 的 attempt 心跳投影采样时间及来源 -> verify: 分别保留原事件 ts 和来源 usage_timestamp，未找到同任务来源不伪造时间。
2. 监工提示要求区分来源时间、账本时间和真实进展 -> verify: 无来源时保持未知，worker 心跳或用量不冒充结果验收。
3. 测试并部署 -> verify: Run Manager、resident、事件调用方和 mock 流程定向测试通过；LS 的旧用量显示原采样时间。

## 验证

- 229 项定向测试、53 项哨兵、10 项 mock 流程通过，合并后定向 8 项通过。自动规划器因 161 个映射文件建议完整 pytest；本变更仅涉及 Run Manager 的只读信号和提示，按仓库分域规则执行上述定向闭包，未运行全量 pytest。
- LS 控制面重载后，06:34:11Z 实际 `run_manager.json` 显示旧样本 `evt-469992c4b004` 的采样时间 2026-11-01T08:37:46.232Z 与入账时间 2026-11-02T06:33:08Z；身份任务亦保留相应采样时间。
