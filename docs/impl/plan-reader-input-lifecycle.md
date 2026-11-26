# Plan Reader 输入生命周期与正式派发一致

关联 [198](../design/198-context-index-write-read-resume.md)、[199](../design/199-worker-failure-recovery-and-role-handoff.md)。

LS-v1 的 FMS03 恢复 Planner 通过正式预注册进入执行，但 `artifact index/recover` 将它冻结的历史 PlanPackage 视为失效，要求重绑定。Planner 据此拒绝继续，提交 `evt-3af8cfa13854`，准入 `evt-578f938ea7f2`。OA 对缺失任务输入分区的诊断成立，故障发生在控制面：handoff 允许 Plan Reader 读取历史已准入基线，输入生命周期却只允许当前包或 task-domain 例外。RM 收到合法的 abstained 后重试同一基线，无法修正这个机械判断冲突。

Plan 是基于旧证据提出修订的角色；输入基线可以是历史已准入包，输出仍须经过完整计划准入。`input_lifecycle` 现在复用同一语义：先通过既有 PlanPackage reducer 证明 ref/digest/generation 历史准入，再由 `plan_reader_input_binding.current_plan_reader_baseline` 证明唯一活动 Plan Reader 的不可变请求、manifest 摘要、operation/attempt/dispatch/lease/role/Run 与结果身份完全相符，且该 fanout 没有取消。满足时返回 `pinned`，不要求重绑定。

该判断只校正已派发 Reader 的读取投影。它不把历史包变成全局当前包，不授予重绑定、执行或结果准入权，不改旧快照，不允许 Planner 覆盖并行任务的新合同。其他任务、非 Plan Reader、伪造 manifest、请求摘要不符、lease/attempt 变化、取消或已终结 operation、未准入或已撤销的计划仍保持拒绝。

新测试通过真实 OA return_to_plan、Reader prepare/started 和公开 `artifact index/recover` CLI 串联，先 3 失败 12 通过。真实 155592 条事件前缀只读回放：旧 `superseded/requires_rebind=true`，修复后 `pinned/current_plan_reader_baseline`；同一个已终结旧 attempt 在当前事实下仍需重绑定，不能复活。`authorizes_execution=false` 与 `current_memory=unknown` 的保守含义保持。

实施、部署与现场后继结果见 [任务](../../tasks/2026-11-08-1551-plan-reader-input-lifecycle.md)。

## Plan Reader 的受控 CLI 分页

已启用原生输入工作区的角色优先使用挂载入口。需要经 CLI 强制读取时，`workflow-read` 的 Plan Reader 可以在工具输出容量足够时从 `--format text --page-bytes 16384` 开始，`plan-synth` 沿用 32768 B 宽页。判断路径使用已绑定的读取 profile 与 Plan domain，无需固定角色名称。每次返回一页完整原文，能设置原生工具输出预算时预留至少 16384 tokens 并检查工具包装与实际回包；容量未知或较小时使用 8192 或 2048 B。遇到截断以原输入游标减小页长重读，首页不传游标。原文留在获准的角色会话，对外说明引用来源定位。分页参数仅调整交付形式，旧 WRC、受控选区与完整读取证据仍按原合同核验。[角色与回退验证](../../tests/test_artifact_read_briefing_policy.py)

## 返工描述符的按需读取

新的 Plan 读取请求在同一 Run 的 kernel 失败事件已绑定全部历史候选，且当前 manifest 对每项都授予内容相同的独立完整读取时，可将 `plan-rework-context.v1` 的整根必读改为四项。四项分别是 `schema_version`、`rework_of`、`rework_attempt` 和完整 `rework_feedback`。历史候选逐一核对文件、摘要、授权路径和完整内容绑定。根文档保留原历史 occurrence、事件与版本元数据，可按 `$` 回读。任何未知字段、受限授权、历史身份变化、缺少独立来源或显式必读要求，都沿用原策略。旧 operation 以已登记的读取政策和请求摘要重放。实际减少的引文字节按两个选区的完整文本计算。[受控读与重放测试](../../tests/test_plan_rework_descriptor_reads.py)

Owner 从已接受的计划启动新一代返工时，还可核对本次 Web 批准、代际回执、该计划的 Critic 准入及其签署的来源清单。经授权的原地恢复同样需要追溯原批准。每个历史候选的原路径、摘要、权限和当前读取义务必须有对应项。如果某些字段已按内容分区，内核逐一核对本源的必读区段和选区摘要，并验证复用字段与另一个已获完整授权且必读来源的字段内容相同。通过核对后，所有 Owner 指导与完整分区导航继续必读，描述符原貌可在同一来源以 `$` 查询。未知字段、缺少回执、分区覆盖不全或权限不符时继续读整根。

团队章程可以单独要求若干输入。此时只调整隐式必读的返工索引，其他声明连同顺序、来源版本、选择器和约束原样保留。显式指定返工上下文本身时保持声明。新签请求在需要时记录显式声明在最终读取清单中的位置，恢复时以原声明重建同一请求。较早签收的整根策略继续有效。[Owner 指导、分区和请求重放测试](../../tests/test_plan_owner_rework_descriptor_reads.py)

OA 给只读审计分配 Task Verify 时，可列出该任务首站的精确 AC。请求纠正和派发按同一份当前任务合同计算，复用快照的路由与冻结 Candidate 的延后规则。这样首站由独立审计负责的 Candidate-owned AC 可以被点名，已延后的全局 AC 仍由冻结 Candidate 验收。普通任务沿用其 Task Verify 所属 AC。例见[精确分工及真实派发测试](../../tests/test_oa_readonly_verify_scope.py)。

## 机械预检返工的编辑起点

同一 Run、同一阶段和同一授权代内，内核因 `plan_candidate_preflight` 退回已签收的 Planner 候选时，下一份新签发的 Planner 请求可以单独绑定编辑授权。内核沿退回事件定位唯一的签发、结果准入、子任务、控制清单与预检回执；每个文件需要原返工授权允许整文件、在本次输入清单中出现且内容摘要吻合。必读选区、原 ACL 和原 PlanPackage 保持各自的约束。

Planner 从工作区使用 `zf plan draft-baseline --workspace --from-rework --attempt <attempt> --port task_map` 导出所选端口及获准的 `plan.md`，可重复指定端口。命令返回生产者准入事件、控制清单与预检摘要、每个导出文件的 SHA256，以及正式比较包摘要。Markdown 按物理文件逐字节导出，其文本与已签控制正文可仅差一个末尾换行；收据分别列出两份摘要。后续修改仍须满足 Owner 纠正、独立评审和正常的 Plan/Goal 准入。

普通 `draft-baseline` 继续导出已准入的正式比较包；`--seed-goals` 仅适用该模式。旧签发请求按原身份重放。独立 Critic 的评审返工和 Owner 开启新代各有自己的输入授权，本入口只识别上述同代机械预检链。来源不全、跨代或只有部分读取许可时，新参数会明确报错；Planner 可用该次已分配的来源进行修订。[授权与导出测试](../../tests/test_plan_rework_draft.py)
