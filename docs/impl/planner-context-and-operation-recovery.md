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

同一路径的 Task Map 经受控包修复后，writer materialization 在同 Run replan 下还需
比较 package id/ref/digest 和 generation。任一明确新身份变化都走既有合同 Authority
CAS，不能仅按路径相同跳过 backlog / in_progress 任务。未选择的 done 不参与物化；
同身份重复准入不刷新。见 `tests/test_writer_task_package_rebind.py`。

若旧代码已准入代际、但任务绑定未刷新，普通重触发会先命中代际幂等保护。操作员
可对其原 task_map.ready 使用 `stage-retrigger`，设置 `refresh_plan_bindings: true`
及 `approval_ref`。仅接受当前同 Run、尚无任何 stage.dispatched 的冻结代际，核验
Package 摘要与完整 Task Map 后，经 Authority CAS 更新该代际任务的四个版本字段。
任务合同其他字段和状态不变；已派发、跨来源、终态任务或已被替代的代际一律拒绝。

### 重发未改输入的任务分区继承

Planner 可在新 Plan 中显式列举原始输入的相同 ref/SHA，使包将该端口归为 produced。
实际任务交接不再仅凭 produced/inherited 分类决定前驱分区是否可用：当后继缺自身
分区时，沿当前不可变包的 supersedes 引用，核验同 Run、同 Run contract，以及
端口 logical_name/ref/SHA/schema 完全相同，建立只读内存来源绑定，再走既有显式
Task 替换关系校验。多层后继同样逐边证明；没有父包、正文或ref变化、跨Run/合同、
前驱歧义均拒绝。冻结包、读取回执和输入正文不改写；Candidate/global读取范围不变。

入口为 ArtifactQueryService -> current_plan_port_sources；
`tests/test_republished_plan_inputs.py` 经实际服务入口覆盖 Impl/Task Verify 与多层
重发。该修复使已批准的同版重发可原地重试，不要求重新规划或复制完整需求正文。

### OA 过期结果必须收口 operation（2026-11-07）

`workflow.call.result.invalid` 的 `stale_call_result_superseded` 不等于 operation 已终止。OA 准入入口在拒绝过期包后，核验不可变 request、当前作用域包、结果 envelope 的 Run/request/attempt 绑定，通过 `WorkflowOperationService.supersede` 收口。每轮 pending checkpoint dispatch 也重放同一规则，修复历史遗留 running 占用；queued 且已过期的 checkpoint 不再付费派发。当前包、被保留 Task 的旧包、缺失或损坏证据及尚未提交结果的运行中 OA 均不自动退役；plan_candidate 沿用已有候选版本替代协议。该机制不修改业务 Task 状态，也不把过期结果当作通过或触发新 Planner。

### 跨代际 gap 的显式输入比较（2026-11-07）

旧 Task 的局部恢复以当前已准入完整图为修改基线，保留其他任务的新进展。除 Run、合同、不可变包祖先、原任务正文（或已证明的依赖替换）、共享约定和 Owner 决策外，始终核对 requirement_spec；其余输入按任务显式 plan_input_selection 的 Impl/Verify 联集校验摘要及正文。required_plan_ports 表示完整计划必须交付的输出，不表示每个任务都消费全局 execution_spec。未选择的全局执行说明更新不再误阻塞；已选择输入变化仍拒绝。没有显式选择的跨代际恢复保持拒绝。冻结旧包及结果不修改。回归由 tests/test_gap_plan_baseline.py 经真实 gap bridge 覆盖，验证保留当前图中无关任务和同源幂等。

### 当前任务合同的必读覆盖（2026-11-07）

Impl、Task Verify 和 Candidate Verify 对已绑定的 `contract` 快照读取完整 `$`。
该对象已经是当前任务合同；只读 AC 和命令会漏掉 `evidence_contract`、输出及范围约束。
计划输入仍按 `shared + 当前任务分区` 读取，不扩大成完整计划扫描。
仍通过 Required Read CLI 分页及 digest/连续页校验；此规则只用于新声明，既有 attempt
已冻结的 required-read policy 不回写。读取完成仅证明输入交付，不证明理解或验收通过。
证据是否满足当前阶段合同由交接/Verify 方法逐项判断；不会自动将已有命令通过当成
额外文件要求已满足，也不根据历史目录缺失自行篡改已接纳结果。

### OA 正文投递与读取收据（2026-11-07）

OA 检查点 briefing 优先提供同 attempt 的 `artifact read --required` 分页入口；
保留单源命令供补读。方法由 `yoke/context-hygiene` 约束：消费正文后继续下一页，
后台 capture_output 循环的计数不能代替正文；CLI 已读取不证明模型已理解。
不更改 required/optional 来源、已冻结 policy 或准入判定。回归通过真实 OA operation
与角色读取能力执行生成命令，验证分页正文与全部必读源一致。


## 声明证据与写入范围对账

Planner/增量恢复和 Critic 应把每份证据对应的生产阶段、写入者、路径及消费者写入既有评审，
并检查允许写入范围。evidence_contract 不会自动扩大 operation 的写入权限；
未提交文件也受 scope hook 检查。独立 Verify 的报告沿现有受控结果入口发布。
方法位于 zf-plan-task-map-contract 与 execution-readiness 的 execution-consistency 参考，
不新增项目专属 Runtime 推断或第二份任务合同。历史指定文件缺失与产品测试失败分别记录；
修复未来范围不能补写历史验收证据。

Verify 生成返工 `allowed_scope` 时，同样对账产品修复及原合同授权的必需证据输出。
Impl 的返工代码清单不删除原合同证据义务；Task Pipeline 初次派发及返工提示均说明
此边界。显式写入禁令须澄清，原合同不允许的路径仍需受控修订，Runtime 不自动扩大
scope 或改写已接纳反馈。方法归属 test-evaluator 与 done-contract 技能；既有已结算
失败保持不可变，后续恢复保留原修复提交和独立验收。


增量 gap writer 会保留提案显式提供的 description、source_excerpt/source_ref、
owner_decision_ids、lane_affinity、skills_required、explicit_non_goals、exclusive_files
及结构化 verification。仅携带本次提案内容，不猜测旧任务语义、不扩大权限；
空值和无效值保留给现有准入检查，不能静默丢弃技能声明而绕过拒绝。
发布后的 Task Map 经实际 writer_task_items 消费验证，源提案保持不变。


当原输入没有后继分区，输入继承需跨过保留该任务的无关增量包，找到实际声明替换边的包。
每一层仍核对不可变端口/ref/digest 与 Run；同时回放完整任务声明和已准入依赖改写。
中间任务消失、歧义身份、语义漂移或输入变化均拒绝，不退回全局读取或跳过来源校验。


同批一对一替换多个任务时，只重绑定旧新节点和原入边；下游任务的外部前置仍属于该下游，
不能传递到最早上游而制造额外串行或环。一个旧任务拆成多个后继时继续使用原拆分继承规则。


### 已替代任务的迟到恢复失败

Reader 的失败仍保留原事件和证据，但在 stage replan 与 Run Manager 升级收敛前，核对其精确 fanout→trigger→原失败因果。仅当同一 Run 的 `task_map.ready` 针对同一原失败声明完整替换，并且该 ready 对应的 WRC `task.pipeline.generation.admitted` 已接纳所有后继时，判为 `superseded_by_admitted_task_replacement`，不重派旧恢复、不将当前 Run 标为 blocked。新计划版本、运行中的其他 agent 或尚未准入的提案均不足以证明已替代。拆分后继未全部接纳、仍保留旧任务、覆盖不完整或跨 Run 都继续原失败处理。

旧版本已产生的 blocked 不自动抹除；operator 必须核验替代关系，通过精确 `human-decision-dismiss` 和 `zf goal set --status active --reason ...` 恢复。该动作不代表产品验收通过，也不重写旧 attempt 或新任务合同。回归覆盖真实 Reactor 入口与 RM 终态收敛，见 `tests/test_reader_failure_supersession.py`。

同一因果证明也用于 Reader pending/recovery 派发入口：WRC 已完整接纳后继且旧任务不在其中时，沿既有 `fanout.cancelled`、Operation cancel 和精确 dispatch 的 provider 停止通路退出旧诊断。仅拒绝旧结果不足以收口，否则 `provider.turn.closed` 会被误判为丢失交付并重派旧 Reader。判定合并 trigger 的 affected tasks 与各 child 的任务身份，任何未被承接的任务都阻止整组取消；不依靠“出现了新计划”猜测替代，也不生成通过结果。重启 tick 幂等及未准入、异 Run/原因、部分替代等反例见 `tests/test_reader_replacement_closeout.py`。


### 诊断通道互相独立与已关闭升级

当历史 no-progress 诊断受 Autoresearch 单飞占用约束时，Run Manager 允许当前任务的只读诊断送到空闲 resident；继续沿用任务范围、人工门禁、恢复准入和单次消费检查。resident 已有待答请求、不可用或任务有人工门禁时不绕过；同理不得借此派发任意 replan/重试。原后台调查及队列保留，未把发送诊断当修复完成。

精确 `human-decision-dismiss` 产生的 kernel `human.escalation.acknowledged(status=dismissed)` 会从 no-progress 当前投影中排除所绑定的单条升级。它不删除事件、不重置整个指纹/Run 的失败预算；其他未关闭升级仍计数，新失败仍可熔断。仅请求、非dismiss状态、跨Run引用及外部ack均不清理计数。
