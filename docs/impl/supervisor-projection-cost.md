# Supervisor 快照中重复历史扫描的收敛

本修复针对 LS-v1 监工实测：一次快照包含约13.8万条事件，任务阶段统计反复把全部历史传入代次筛选，失败信号为每个带 fanout_id 的事件重新构建身份投影。带 cProfile 的样本约69秒，其中自动化投影约35秒、fanout身份投影约13秒；这些是采样数据，包含 profiler 开销。正常 Run Manager tick 约27秒的问题在本轮派发诊断修复之前已经存在。

## 实现边界

- `automation_projection._task_board_counts` 在每次调用内按 `event.task_id` 分组，按原顺序保留该任务全部代次。随后仍调用 `derive_phase` 和 `task_pipeline_event_scope`。阶段派生最终只接受本 task_id 的事件，dispatch anchor 也只来自本任务；所以去除其他任务/全局事件不会改变范围锚定、同代 impl/verify 或晚到旧结果的判断。
- `fanout_identity.fanout_current_statuses` 从一份完整事件序列构建所有已知 fanout 状态。`detect_fanout_failures` 在一次调用内复用该结果。单实例查询保持原 API；无 started 的 legacy fanout 仍按未知但当前处理，不吞掉失败。
- 不增加持久缓存或跨 tick 缓存，不截断历史、不按时间猜测完成；下一次调用重新读取输入并计算。语义成功、终态、supersession、pending grace 和恢复在途规则继续由原实现决定。
- 实际调用入口为 `project_automations` 和 `collect_failure_signals`，由 `build_supervisor_snapshot` 消费。没有新增状态机或自动修复权限。

## 验证

新增 `tests/test_supervisor_projection_cost.py` 从真实投影入口覆盖看板结果与完整扫描一致、旧代次结果隔离、legacy fanout、后继替代、下一次调用新事件可见，并给重复计算设确定性工作量回归约束，避免用机器相关的耗时阈值判断正确性。沿用自动化、失败信号、fanout身份和任务阶段测试。

真实项目138164事件固定输入回放中，看板与fanout失败信号完整输出一致；两个热点合计17.204秒降至0.331秒（单次无profiler、并发负载样本）。不把单次 profiler 数据当持续吞吐保证。记录位于业务项目 `docs/records/2026-11-08-control-plane-cost/`。本轮只收敛已证实的两个重复计算点，workflow reader checkpoint 等其他耗时仍需单独证据后处理。

实施任务：`2026-11-08-1124-supervisor-projection-cost.md`（验收后归档到 `tasks/done/`）。
