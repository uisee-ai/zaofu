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

显式 reader 执行失败归一为 `failed/abstained` 时，允许必读覆盖不完整，
封存实际读取账本（尚未读取则封存空账本），并记为 `input_consumption.status:
incomplete`。这不是读取完成证明；账本摘要、格式、attempt 身份及受控提交授权
仍校验，非法账本不能借失败路径通过。完成的 reader 结论，包括语义拒绝，
继续要求完整必读覆盖。回归：`tests/test_replan_input_consumption.py`。

显式指定受影响 task ids 的返修，必读上下文保留全部任务的状态、依赖、合同身份、
交付/阻塞事实及受影响任务完整正文。全量 canonical TaskStore 快照独立封存并
登记为 `canonical-task-snapshot` 来源，索引提供精确 JSON path 和摘要；Planner
修改其他任务合同或解析未知 task id 前需读取该任务正文。未指定范围的重规划
仍必读完整快照。此分区不改变任务、AC 或预算，也不允许用索引代替完整 task map。
