# 磁盘用量重放不得刷新Worker活性

> 状态: done
> 实现: `e596151f fix: reject replayed usage as fresh worker activity`

用户已授权8×8发现问题后修复、记录、恢复。仅在隔离分支实现，不merge dev、不push。

实际lane2最后工具/读取约00:26:14；控制器00:33和00:44重读同一usage_timestamp，新的agent.usage事件却刷新RoleSessionRegistry心跳，掩盖了沉默。不是已确认Provider故障：当前角色保留600秒阈值和2400秒长turn宽限。

- 历史/重复/无效/未来disk usage -> verify: 经Orchestrator housekeeping不得刷新活性，计费仍记录。
- 真实新sample -> verify: 刷新活性和上下文观测；重建controller后重放不得再延期。
- 通用activity兜底 -> verify: 不先刷新disk usage绕过精确时间检查；普通hooks和实时usage路径保持。
- 实际部署后观察 -> verify: 旧sample不再重置心跳；不缩短阈值、不强行判产品失败、不绕过attempt上限。

验证：原始主路径行为RED5 failed/1 passed；发现异常兜底仍绕过检查后另存RED5 failed/6 passed。最终96项focused通过，最终自动增量影响闭包4301 passed/7 skipped，sentinel53 passed、flow smoke10 passed。计费保留、当前上下文比例保持、控制器重建与兜底路径均有回归；真实投影已签收：00:57:34 控制器重放 provider 样本 `00:26:14.248Z`（`evt-fe94f1898e5c`），lane2 心跳前后均为 `00:44:10.775125+00:00`，没有再次延期；01:01:09 原有长 turn 宽限结束后产生 `worker.stuck`（`evt-1a0351821346`）。此处只证明正确暴露失活，不宣称后续恢复完成。

真实证据：`reports/eight-qualification-20261120T122929Z/disk-usage-liveness-real-proof.json`（部署 worktree）；包含 before/after 心跳及重放样本。无 Worker 重启、无阈值放宽、无产品终态补造。
