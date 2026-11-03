# 计划准入失败后恢复候选来源

> 状态: active

LS `evt-5a5ce34d9618` 因当前 `task_map.ready` 的 writer 基线冲突而失败；隔离演练发现新代次绑定最新 task map 和 Plan Package，却同时携带上一轮的 `previous_plan_candidate_refs`，可能误导 Planner。

- 确认同 Run 的 `task_map.ready` 触发计划准入失败 -> verify: 不继承更旧 trigger 中的候选 refs，仍绑定当前 task map 与 Plan Package。
- 当前 failure 显式提供已校验候选 refs -> verify: 继续采用该次 refs；非计划准入和无当前 task_map.ready 的普通重试维持既有行为。
- 实际 controlled action 隔离重放 -> verify: 使用真实 LS 事件副本和受控动作，仅生成新代次与明确 feedback，候选引用不混代；原状态不改变。

验证：`tests/test_stage_failure_replan.py`、`tests/test_control_actions_recovery.py`、`tests/test_reader_replan_feedback.py`、`tests/test_plan_candidate_preflight.py` 共 91 passed；使用 LS 事件在线副本执行受控动作的隔离演练成功，触发指向当前 Task Map 与 Package、旧候选 refs 为空、两条反馈保留；`simulation.done` 后模拟状态已删除。sentinel 53 passed。
