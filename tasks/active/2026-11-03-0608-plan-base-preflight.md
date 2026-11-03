# 计划预检对齐实施派发基线

> 状态: active

LS Run `prepare-TASK-90D92C-e4eaededbd2b74fe` 的 Planner 候选同时包含 `66e1319` 与 `9cc4fd9` 两个任务基线。现有 Plan 预检未报告冲突，Critic 完成 3 小时审查并批准后，writer fanout 准入才拒绝；原 Run 随重规划上限耗尽进入 blocked。该任务仅修复可复用的机械预检缺口，不修改运行态、既有计划或豁免语义审查。

- 全量 writer fanout 的任务基线不唯一 -> verify: Plan 候选预检在 Critic 派发前使用同一 writer 准入规则拒绝，并包含诊断。
- 显式 task_ids、gap 继承和 wave 选择 -> verify: 预检使用与实际 writer 相同的选择范围，未选任务不引起误报；非法映射仍 fail closed。
- 单一基线或无任务基线 -> verify: 既有合法候选行为不变。
- 真实控制面调用链 -> verify: 定向预检与 writer 准入测试、premerge sentinel 和相关回归通过；变更不宣称旧 Run 已恢复。

验证：真实 LS 当前候选 55 项在同一 writer 选择函数中重现冲突；`tests/test_plan_candidate_preflight.py`、`tests/test_writer_fanout_admission.py`、`tests/test_writer_fanout_runtime.py`、`tests/test_plan_synth_handoff.py`、`tests/test_replan_authority_inputs.py` 合计 261 passed；新增选择测试 2 passed，sentinel 53 passed。影响闭包广回归 14260 passed、20 skipped、7 failed；失败为新 worktree 缺少未跟踪 export 脚本和 prompt（5 项）、已有飞书测试失败（1 项）、mock 流程仍依赖 Claude 登录（1 项）；serial 10、flow smoke 10 passed。
