# 心跳任务归属校验

> 状态: done — `b16b96b8 fix: validate worker heartbeat task bindings`

来源：LS-v1 2026-11-08 19:57:28，dev-lane-1 将 FMS-DB-READINESS-01 错写为 FMS-READINESS-01，evt-b05c9facac3f 被 CLI 接受；20 秒后 evt-11465cdd5f85 自行纠正。用户已授权监工期间发现、修复并记录问题。

范围：仅活跃 TaskAttempt 心跳的机械归属/显式身份校验，CLI 入口与 runtime housekeeping；无 TaskAttempt 的原有角色心跳兼容。保留原错误事件，不直接编辑生产 runtime state。不改变任务合同、模型、派发或 Owner 决策。

验收：
- 当前 HEAD 隔离复现误任务心跳 -> verify: CLI 应拒绝而旧实现接受；runtime 不应覆盖原活性而旧实现覆盖。
- 校验活跃任务、actor、current_task_id 及显式 attempt/lease/dispatch/run/operation -> verify: 错误 CLI 非零且不追加心跳；绕过 CLI 的错误事件在实际 Orchestrator caller 拒绝，租约/活性/去重保留并有审计。
- 保留正确 CLI 自动绑定与无 TaskAttempt 原有心跳 -> verify: 正向和 legacy 回归通过，使用自定义 state_dir。
- 完成影响闭包、哨兵、相关确定性 mock E2E -> verify: 记录具体 tier，不等同真实 Provider E2E。
- 合入 dev，受控加载，不中断活跃 worker -> verify: 在途 operation/attempt 保持，正常心跳继续；记录原问题与修复证据。

## 验证记录

- 当前 dev 隔离复现：2 项 RED（CLI 接受拼错任务，housekeeping 覆盖 lane）。
- 修复初版定向 104 项通过；最终影响闭包 208 项通过，覆盖 CLI、心跳/扫描、TaskAttempt 租约/恢复/结算、实际 Orchestrator 与 Event/Store 锁。
- dev-verify plan 因 Orchestrator 文件反向导入列出 316 文件并退化全量；遵循 AGENTS 按行为闭包而非数量触发全量的规则，本轮显式跳过 broad deterministic/serial，改跑上述 11 个直接相关测试文件。未更改 planner/selector。自动 contract 哨兵 53 通过、三流/流水线 runtime-noise mock E2E 10 通过。无真实 Provider E2E、无全量 pytest。
- live 只读评估确认 3 个仍活跃 worker 的最新真实心跳通过；DB worker 此时 Impl 已结算，其旧心跳按 terminal 拒绝。历史错误任务在其 lane 已无活跃 TaskAttempt 时属于 legacy 无匹配范围，因此不能用该时间点的只读检查宣称重现原活跃态；原活跃态由隔离 2 RED/后续回归证明。

合并 `2d1856c8 fix: prevent stale heartbeat liveness updates`；合并后哨兵 53、CLI/心跳 74 通过。
2026-11-08 20:08 UTC 通过 source_repair_restart_actuator 重载，Watcher 3337330、Web 3338084（HTTP 200），均加载 2d1856c8。四个在途 operation/attempt/dispatch/workdir/pane/PID 逐项相同；没有重派或全局 replan。新 Verify 自己发出的 evt-caffcb02705c 经新 CLI 正常接受，runtime 以 evt-d22859c9178c 续租；无生产伪造心跳，无新的绑定拒绝。完整现场记录保存在 LS-v1 docs/records/2026-11-08-heartbeat-binding。
主 worktree 原 AGENTS.md 与 design index 修改摘要保持；无 push。归档后的正常 worktree 清理另记现场记录。
