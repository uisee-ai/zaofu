# on-demand Verify 完成后的心跳归属

本片修复 `worker.stuck` 的机械归属检查，不修改上下文阈值、心跳时限、恢复预算或业务验收。任务：[2026-11-07-1046](../../tasks/done/2026-11-07-1046-idle-verify-stuck-ownership.md)。

## 实况与原因

2026-11-07 UTC，Media 独立 Verify 在 06:20:25 以 `evt-f22cf3c600c5` settled/passed。其 integration 在 06:23:44 因 rolling smoke 失败 blocked，这是独立的候选环境问题。

已经没有活动工作的 `prd-verify-lane-1` 仍被空 `current_task_id` 的旧 busy 心跳判为 stuck。原 `_heartbeat_current_task_still_owned` 对空 task id 返回 true；后续 `_heartbeat_worker_finished_current_dispatch` 在没有活动 Task 时返回 false。恢复因此继续重启空闲 provider，达到 respawn success circuit 后于 06:47:38 和 06:55:28 发出 `dispatch.paused`：`evt-1efa8d04d7b6`、`evt-ba090cbf5766`，原因均为 remediation cascade 的 `safe_halt/worker_stuck`。

Retention 的 Verify 于 07:04:34 通过，但 `prepare_task_pipeline_dispatch` 在全局暂停时直接返回，integration 和 pipeline projection 对账都没有继续。此时 pool 容量仍有空位，Media 的单项 blocked 并未触发 integration queue backpressure。07:17 空闲 Verify 已休眠，项目级暂停仍须通过受控恢复解除。本片不自动解除已经发生的 safe-halt。

## 最小变更

`heartbeat_work_obligation.py` 在 `on_demand` 角色上先核实活动义务：

- 精确 `role_instance` 上 running 的 typed workflow operation；不要求 reader 父 Task 处于 in_progress。
- 当前 prepared/delivering/sent TaskAttempt；已知 terminal/suspended 或换属其他实例的 typed operation 否定遗留 attempt。
- legacy 活动 Task/fanout child；已有 typed terminal child 不因缺少旧 fanout completion 再变成活动义务。

确认无义务时，真实 heartbeat sweep 的 silent/stuck 两条路径都停止误报；`_report_stuck_worker` 入口共享同一检查，覆盖 pane 和直接恢复调用。resident/eager 角色保持原检查，不能把无 Kanban Task 等同空闲控制角色。读取失败保留原检测，不把未知状态当作确认空闲。

原 heartbeat ownership 方法抽到独立模块，避免继续扩张 orchestrator。没有新增 canonical store、事件类型、缓存或持久状态。TaskAttempt/EventLog/TaskStore 仍是既有权威来源。

## 验证

隔离工作树当前 HEAD 上先 RED：11 failed / 6 passed；其中包括已 settled Verify 的空 task busy 心跳误触发、typed reader 父 Task backlog 被旧 guard 漏判，以及 direct stuck 入口误恢复。最终新增测试 26 例。

Focused closure：下列 15 个文件共 **203 passed**（36.81s，`-q --no-cov`）：

```text
tests/test_heartbeat_work_obligation.py
tests/test_heartbeat_sweep.py
tests/test_stuck_dedup.py
tests/test_worker_heartbeat.py
tests/test_proactive_idle_dispatch.py
tests/test_provider_turn_liveness.py
tests/test_task_pipeline_worker_liveness.py
tests/test_lifecycle_fanout_child_queries.py
tests/test_stuck_livelock_fixes.py
tests/test_stuck_detection_integration.py
tests/test_transport_stream_json_stuck_parity.py
tests/test_interrupted_recycle_recovery.py
tests/test_capture_logs_watchdog.py
tests/integration/test_lifecycle_full_chain.py
tests/integration/test_context_recycle_e2e.py
```

Sentinel **50 passed / 1 skipped**；自动 tier 的 contract 同样通过，deterministic flow smoke **10 passed**。`dev-verify plan --base dev` 将此前未合并批次计入并选 784 个测试文件；`--base HEAD` 仍因 orchestrator 广泛映射选 175 个文件，均升级全 deterministic pytest。本片按局部影响闭包替代 broad deterministic/serial，实际运行 `dev-verify run --base HEAD --skip-tier deterministic --skip-tier serial`。没有声称全套验证或真实 provider E2E。

另外只读提取实况前缀中的 6 条 canonical operation 事件（包括 Media 两次 Verify 的 requested/started/settled），写入独立临时测试状态；重建事故的空 task stale busy mirror，对照 264f6b35 原 guard：旧结果 true，新结果 false，新 sweep 没有 stuck 或 pause。原始事件 ID 保留在日志。此验证不是 provider transcript 全量回放，也没有向生产事件或 read receipt 写入；临时状态已清理。

证据目录：`/tmp/zf-supervision-20261107/idle-verify-ownership-{red,focused,sentinel,auto,real-replay,plan-dev,plan-head,read-cost}.log`。

## 8+8 lane 的读取成本

已有 `EventLog.read_all()` 的进程级 mtime/size/签名身份缓存与 append-fold 会复用已解码事件，不是每个 lane 重新读取并 JSON 解码全部日志。本片直接复用该入口，没有额外缓存。对实况 110,359 条事件、249 个 operation 的只读测量：冷读取 3.889s；5 次热读取 0.579–1.227ms；operation fold 31.250–38.001ms。按中位数估算 16 个 lane 都需检查时，读取和 typed fold 部分合计约 0.582s；不包含 TaskAttempt/legacy 查询，也不是整个 tick 的耗时。

检查只发生在 heartbeat sweep 的 silent/stuck 候选和 direct stuck 恢复入口；健康 lane 不额外执行该检查。现有 operation reducer 本身没有共享缓存，后续若实测该部分成为瓶颈，可在单次 sweep 的同一事件快照内复用 typed fold；需独立验证追加事件、operation 换属及后续 recovery 再检查的新鲜度。本片不新增长期缓存或修改状态更新顺序。
