# 恢复案卷承接证据

`recovery_case_file` 是供 Run Manager 判断的只读事实投影。它使用既有 fanout
identity 排除已被取代的 fanout 事件，并按任务与 fanout 核对在飞 child；其他任务
或已关闭 fanout 的 child 不构成当前任务的承接者。

旧 reader 的终局记录不能覆盖已经发生的 Task Pipeline 责任转移。仅当当前 Run 的
最新 `task.pipeline.generation.admitted` 同时绑定原 fanout、父任务、非空 generation、
Task Map generation 和任务集合时，案卷将其展示为 `continuations`，附原 child 事件、
准入事件、Run 和 Task Map generation。单独的 Plan 审批或其他任务忙碌不满足这一条件。

没有这些证据时继续保留 `wip_without_carrier`，并附来源事件、fanout 与 workflow
generation，供 agent 选择是否诊断或恢复。该投影不裁定语义质量、不修改任务状态，
也不自行执行 requeue、restart 或任何受控动作。真实 tick 回归覆盖写出与状态不变。
