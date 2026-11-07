# Reader 上下文与终止操作恢复

Codex 的手动 `/compact` 需要空闲会话。长时间执行的 reader 在默认原生压缩发生前，
可能先达到 ZaoFu hard cap，并在 drain hold 到期后被回收，丢失会话内的继续工作线索。
`CodexAdapter.build_command` 现在为 fresh/resume 都传入原生自动压缩预算：
`max(1, int(role.context_window_tokens * role.context_compact_threshold))`。
默认是 140,000 tokens；这是配置窗口的保守工作预算，不是模型实际窗口声明。
不覆盖 `model_context_window`，不修改用户全局配置，context hard cap 和预算熔断保持原规则。

[Codex 配置参考](https://learn.chatgpt.com/docs/config-file/config-reference)
定义 `model_auto_compact_token_limit` 为自动历史压缩阈值。模型窗口较小或单次工具输出
很大时，Provider 自身上限和 harness hard cap 仍可能先触发，不能将此设置视为完成保证。

Reader 恢复次数耗尽时，内核写入 `fanout.child.failed` 后、评估聚合前，通过既有
`WorkflowOperationService.fail` 终止该 child 绑定的 operation。复用超时路径的
`fail_reader_child_operations`，保留请求身份、因果链和终态幂等。失败 operation
不再被预算守卫视作运行中，也不会继续累计同一角色后续工作的成本与采样次数。
不接纳成功结果、不清零成本账本、不改变允许的恢复次数。

回归由 `tests/test_reader_recovery_operation_closeout.py` 经真实 Orchestrator
入口验证，包含耗尽后的终态、因果链、重复 tick 和后续同角色用量；adapter 测试
覆盖默认/自定义预算以及 fresh/resume。实际长任务仍须观察原生压缩和产物推进。

当 Run 终止连带取消后续 reader 时，监工可先经 `zf goal set --status active`
恢复目标，再通过 `stage-retrigger` 指定原触发事件。该动作除既有固定事件外，
识别当前配置中 `topology: fanout_reader` 的已注册 trigger；沿用原事件的全部
产物、任务、Run 身份，生成新 `rework_of` 代际，同源重复恢复被拒绝。
未注册事件及新增 writer trigger 不因这项支持而获准。
Run 激活与重触发都保留审计，不清零原 Run 成本、不修改既有操作的终态。

Owner 授权的 `stage-replan-new-generation` 可在 action payload 中提供
`input_task_ids: ["TASK-A", "TASK-B"]`，明确需要完整读取的任务合同。该字段只影响
Plan handoff 的读取范围，不替代 `task_ids`、依赖或验收。现有 scope 与明确失败任务
仍保留；全部任务的身份、依赖和状态进入索引，完整合同通过不可变
`canonical-task-snapshot` 侧车与 `full_task_json_path` 按需检索。未知 id 显式列出，
修改其他任务前仍需读取其完整合同。未提供任何范围时保留全量读取。

字段须为非空字符串列表，规范化后绑定到新触发事件和冻结 rework context。同代际
重复请求范围不同则拒绝；后续新代际未提供该字段时不沿用上次操作员的范围。
固定输入的 `repaired_failure_event_id` 重试不接受范围变更，需走授权新代际。
已有活跃 operation 的 manifest 不会被改写。回归见
`tests/test_reader_replan_feedback.py`、`tests/test_replan_input_consumption.py`。

普通计划退回也支持相同的读取提示：`zf plan reject <plan-id> --reason <反馈>
--input-task <task-id>`，可重复 `--input-task`；受控 `plan-reject` payload 使用
`input_task_ids`。范围绑定正式 `plan.rejected`，通过实际 resynth 和 handoff
进入索引；不改变派发集合。未提供范围的新退回不继承上轮提示。
`tests/test_plan_rejection_input_scope.py` 覆盖 CLI、受控动作和下游快照的完整路径。

`flow.gap_plan.ready` 仅在内核记录对应 `task_map.amend.failed` 且尚无成功
`task_map.amended` 时允许受控重触发；原计划内容不变，同源仅可重触发一次，
新事件仍经过正常 Task Map 校验和准入。

停滞检测与派发可以观察到不同批次的事件：补发时原触发尚未消费，真正派发时
原 fanout 已启动。Reader/Writer 派发入口因此按 `original_trigger_event_id`、
事件类型、关联运行与完整载荷核对自动补发及原触发；同一来源在该 stage 已有
fanout 时仅写幂等 `fanout.retrigger.suppressed`，不创建新 fanout 取消正常工作。
两种消费顺序都适用。受控的新 rework/retrigger、不同载荷或其他 stage 保持独立，
未处理过的原触发仍可由停滞恢复启动。见 `tests/test_stall_fanout_idempotence.py`。

`zf plan inspect-execution` 与运行时共用 `flow_metadata_for`：多 Flow 使用
flow_metadata_by_kind 的指定条目，单 Flow 回退到编译后的 flow_metadata。
检查工具不得因单 Flow 缺 by-kind 容器而把已启用的 Task Pipeline 误报为 off；
它仍只报告事实，不批准 Plan、不派发任务，也不创建运行状态。

### Reader / Synth 的活跃回合闲置宽限

Fanout 的 Codex reader 和 synth 在派发后有尚未关闭的 provider turn 时，若压缩上下文等操作暂时不产生 hook，以最后一次实际活动开始计算有界闲置宽限：最多为当前闲置阈值的四倍。该 turn 必须属于当前角色并开始于本次派发之后；关闭、回收或旧派发的 turn 不获得此宽限。阶段显式执行超时仍优先生效，持续沉默超过宽限仍按 idle 失败并走既有恢复。事件由现有读取窗口重建，不重复读取整个事件账本。

### 授权新代际与无进展计数

Run Manager 的无进展计数按 Run 和失败指纹隔离。`stage-replan-new-generation`
实际成功后，以 `runtime.action.completed`、新 trigger、目标重开和旧 escalation
的绑定关系退役对应旧计数。只有请求、失败动作、缺少引用、跨 Run 或重放不清除；
新代际再出现相同失败时重新累计，其他失败与审批门继续生效。历史事件保留不变，
防止旧诊断长期占据队首，阻塞已有授权的后续恢复。回归见
`tests/test_recovery_generation_progress.py`，涵盖真实受控恢复和 Run Manager 消费入口。


### 直接 Plan 汇总的输入/输出身份隔离

无 Synth 的 reader aggregate 与 Synth 路径共用新输出身份规则：Task Map ref 变化时，
不沿用输入的 generation、digest、Plan Package 或 Goal Claim Set 身份。下游仍须重新
经过完整包准入。相同 generation 的包复用必须核对冻结 Task Map 正文；不一致直接
拒绝，防止旧矩阵在稍后的任务选读阶段才报错。全文与摘要、私有临时路径不能互换。

历史误绑定可通过已授权 `stage-retrigger` 提供 `repair_plan_output: true` 和
`approval_ref` 修复。仅接受 kernel 的 `task_map.ready`，核验同 Run 的包摘要和实际
正文不一致，保存旧身份作为观察证据，清除输出身份后沿用原完整产物重新准入、审批。
不重新调用 Planner，不修改原 Task Map、port、事件或完成证据。

可附 `retain_completed_task_ids` 保留被本次全量 replan 重开的完成任务：内核要求
同一 map 的重开回执、精确前后 authority revision、归档 done、原 kernel 完成与集成
回执、无后续派发，且除明确的来源/导航绑定外合同逐字段相同。语义保留决定来自
操作员的 `approval_ref`；机械验证不代替判断。所有任务先核验，再经 TaskStore CAS
恢复原完成时间和证据，并记录 `task.status_changed`，不制造新 `task.done`。
完整 map 保留，仅以 `task_ids` 和已有 `resume_scope` 派发剩余任务。部分 CAS 已成功
后重试可继续；有新 authority 或执行尝试即拒绝。回归入口见
`tests/test_plan_output_lineage.py`，已有范围保留覆盖见
`tests/test_writer_fanout_runtime.py::test_scoped_replan_preserves_unselected_completed_task_evidence`。

同日完成、重开、再次完成时，TaskStore.get 从当日归档末尾读取最新记录；索引缺失时
也按最新日期和记录查找。历史归档不删除，避免恢复 CAS 后中断重试拿到旧 authority。
`tests/test_task_store.py` 覆盖有/无终态索引；恢复测试覆盖 CAS 成功、审计追加前中断。
