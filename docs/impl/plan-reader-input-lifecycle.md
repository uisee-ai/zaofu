# Plan Reader 输入生命周期与正式派发一致

关联 [198](../design/198-context-index-write-read-resume.md)、[199](../design/199-worker-failure-recovery-and-role-handoff.md)。

LS-v1 的 FMS03 恢复 Planner 通过正式预注册进入执行，但 `artifact index/recover` 将它冻结的历史 PlanPackage 视为失效，要求重绑定。Planner 据此拒绝继续，提交 `evt-3af8cfa13854`，准入 `evt-578f938ea7f2`。OA 对缺失任务输入分区的诊断成立，故障发生在控制面：handoff 允许 Plan Reader 读取历史已准入基线，输入生命周期却只允许当前包或 task-domain 例外。RM 收到合法的 abstained 后重试同一基线，无法修正这个机械判断冲突。

Plan 是基于旧证据提出修订的角色；输入基线可以是历史已准入包，输出仍须经过完整计划准入。`input_lifecycle` 现在复用同一语义：先通过既有 PlanPackage reducer 证明 ref/digest/generation 历史准入，再由 `plan_reader_input_binding.current_plan_reader_baseline` 证明唯一活动 Plan Reader 的不可变请求、manifest 摘要、operation/attempt/dispatch/lease/role/Run 与结果身份完全相符，且该 fanout 没有取消。满足时返回 `pinned`，不要求重绑定。

该判断只校正已派发 Reader 的读取投影。它不把历史包变成全局当前包，不授予重绑定、执行或结果准入权，不改旧快照，不允许 Planner 覆盖并行任务的新合同。其他任务、非 Plan Reader、伪造 manifest、请求摘要不符、lease/attempt 变化、取消或已终结 operation、未准入或已撤销的计划仍保持拒绝。

新测试通过真实 OA return_to_plan、Reader prepare/started 和公开 `artifact index/recover` CLI 串联，先 3 失败 12 通过。真实 155592 条事件前缀只读回放：旧 `superseded/requires_rebind=true`，修复后 `pinned/current_plan_reader_baseline`；同一个已终结旧 attempt 在当前事实下仍需重绑定，不能复活。`authorizes_execution=false` 与 `current_memory=unknown` 的保守含义保持。

实施、部署与现场后继结果见 [任务](../../tasks/2026-11-08-1551-plan-reader-input-lifecycle.md)。

## 返工描述符的按需读取

新的 Plan 读取请求在同一 Run 的 kernel 失败事件已绑定全部历史候选，且当前 manifest 对每项都授予内容相同的独立完整读取时，可将 `plan-rework-context.v1` 的整根必读改为四项。四项分别是 `schema_version`、`rework_of`、`rework_attempt` 和完整 `rework_feedback`。历史候选逐一核对文件、摘要、授权路径和完整内容绑定。根文档保留原历史 occurrence、事件与版本元数据，可按 `$` 回读。任何未知字段、受限授权、历史身份变化、缺少独立来源或显式必读要求，都沿用原策略。旧 operation 以已登记的读取政策和请求摘要重放。实际减少的引文字节按两个选区的完整文本计算。[受控读与重放测试](../../tests/test_plan_rework_descriptor_reads.py)
