# 磁盘用量重放不得刷新Worker活性

> 状态: active

用户已授权8×8发现问题后修复、记录、恢复。仅在隔离分支实现，不merge dev、不push。

实际lane2最后工具/读取约00:26:14；控制器00:33和00:44重读同一usage_timestamp，新的agent.usage事件却刷新RoleSessionRegistry心跳，掩盖了沉默。不是已确认Provider故障：当前角色保留600秒阈值和2400秒长turn宽限。

- 历史/重复/无效/未来disk usage -> verify: 经Orchestrator housekeeping不得刷新活性，计费仍记录。
- 真实新sample -> verify: 刷新活性和上下文观测；重建controller后重放不得再延期。
- 通用activity兜底 -> verify: 不先刷新disk usage绕过精确时间检查；普通hooks和实时usage路径保持。
- 实际部署后观察 -> verify: 旧sample不再重置心跳；不缩短阈值、不强行判产品失败、不绕过attempt上限。

验证：原始主路径行为RED5 failed/1 passed；发现异常兜底仍绕过检查后另存RED5 failed/6 passed。最终96项focused通过，最终自动增量影响闭包4301 passed/7 skipped，sentinel53 passed、flow smoke10 passed。计费保留、当前上下文比例保持、控制器重建与兜底路径均有回归；真实投影待部署后签收。
