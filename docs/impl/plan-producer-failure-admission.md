# Plan 生产失败的结果准入

Planner 的结构化结果以 `execution_status` 区分产物是否完成。生产失败可以保留
草稿及诊断，并通过签定的 child failure event 接纳；不要求失败声明具备完整
task map、product acceptance spec 等成功 Plan 产物，也不将失败转换为通过。

`result validate` 与 `result submit` 共用同一判断：明确的
`execution_status: failed` 先验证结构化 reader report 的 summary/findings。
授权、operation/attempt 身份、失败事件绑定和下游结果准入继续执行。
其他 Plan 结果仍经过完整候选预检；仅有 `status: failed` 或 `needs_rework`
表示对被审查对象的评价，不能绕过候选校验。

artifact-production 的结构化 report 保留显式执行失败，即使附带草稿或 findings。
旧版顶层事件仅报告被审查对象有缺陷时，继续保留“报告已产出”的兼容语义。
依据与回归位于 `tests/test_plan_failure_submit.py`、
`tests/test_workflow_read_result.py`、`tests/test_call_result_profiles.py`。
