# Task Pipeline 当前代次的任务投影

新 Impl 已派发时，旧代 `dev.build.done` / Verify / Judge 结果不能继续决定
当前任务的阶段、看板列和验证徽标。`core/task/pipeline_event_scope.py` 只读选择
当前派发所绑定的 Run、TaskMap generation 和 operation generation；没有活动派发
时使用最近的 Kernel 派发。证据窗口从同代 Impl 开始，保留同代 Verify 的衔接。

生命周期和 Kanban workflow 投影共享该窗口。新 Impl 显示 `impl_running`，新
Verify 显示 `test_running`；旧代迟到结果、其他 Run / Plan 的同编号结果不参与
当前徽标。没有匹配 Task Pipeline 派发时保留 legacy 行为。EventLog、TaskStore、
历史拒绝和操作结果均不改写，产品验收仍由原验证合同决定。

`tests/test_task_pipeline_workflow_projection.py` 覆盖重做、迟到结果、身份范围、同代
交接和终态证据，并经真实 Web 详情与 snapshot API 验证。共享调用方包括 Web、
Feishu 和 briefing；这只是阶段投影修复，不改变调度或 Provider 恢复策略。
