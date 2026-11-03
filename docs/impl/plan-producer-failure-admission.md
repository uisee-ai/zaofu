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

Plan Critic 使用 `plan-synth` 结果时，只有明确声明
`execution_status: failed`、`failure_class: reader_execution_failure`，且顶层和
报告均为非批准结果，才允许封存不完整读取账本并记录失败。结果仍以 rejected
进入后续流程，不能发布 task map；完成的语义否决、矛盾的批准以及损坏账本
仍不能绕过读取校验。回归：`tests/test_plan_synth_failure_reads.py`。
准入后的 Critic 结果将该失败分类传递至 fanout 聚合和阶段失败事件，供
恢复流程区分执行失败与计划内容被否决；历史事件不被改写。
`reader_execution_failure` 按基础设施失败归类，不自动发起语义重规划，
也不计入后续语义失败的历史次数。已存在的返工 lineage 不重置。
修复输入交付或执行配置后，通过 `stage-replan-new-generation` 的
`repaired_failure_event_id`、`approval_ref`、`reason` 合同受控恢复；
显式 failure_kind、原失败、读取账本和审批门禁保持有效。
回归：`tests/test_reader_failure_kind_recovery.py`。
Critic 执行失败时，恢复从该次派发绑定的不可变 source manifest 与
plan-synth contract 对账当前子结果及工件引用；新引用优先于旧触发的候选，
并保留原有读取范围和 access scope。身份、摘要或来源不匹配时不猜测当前
候选，也不回退到旧候选。回归：`tests/test_plan_synth_recovery_refs.py`。
新代次受控动作可显式指定紧邻的失败 Critic 检查点；必须核对 Run、阶段、
因果链和已准入结果，并拒绝已被更新候选取代的检查点。Planner 自报读取失败
时直接保留该执行失败分类，不误报缺少计划候选。

显式指定受影响 task ids 的返修，必读上下文保留全部任务的状态、依赖、合同身份、
交付/阻塞事实及受影响任务完整正文。全量 canonical TaskStore 快照独立封存并
登记为 `canonical-task-snapshot` 来源，索引提供精确 JSON path 和摘要；Planner
修改其他任务合同或解析未知 task id 前需读取该任务正文。未指定范围的重规划
仍必读完整快照。此分区不改变任务、AC 或预算，也不允许用索引代替完整 task map。

当 `resume_scope=failed_children_and_downstream` 且已列出 `failed_task_ids`，
正文只内联直接失败任务合同；下游派发范围仍完整保留在 `task_ids` 和
`downstream_task_ids`，全部任务索引及未解析 ID 也保留。扩展派发范围不会自动
要求重读全部下游合同，修改任何未内联合同前仍须读取其完整快照。没有直接失败
任务或采用其他恢复模式时，沿用显式选择范围，避免推测语义读取边界。

返修输入同时绑定已批准 Plan Package 正文及其 produced/inherited 端口。
Package、OA 原裁决和 delta 是小体量必读来源；大端口按需受控读取，保持原摘要。
OA apply → resynth → handoff 传递原裁决 descriptor，裁决引用的证据仅从该 OA
操作原有不可变 source manifest 继承，保留访问范围。旧触发丢失 descriptor 时，
仅允许相同 Run、Package、任务图 generation、失败指纹和任务范围的 canonical
`orchestrator.replan_requested` marker 恢复；不匹配或歧义时拒绝猜测。
这只补全输入接线，不修改历史 Plan、审批、验收门禁或预算。
回归：`tests/test_replan_authority_inputs.py`，真实入口覆盖 OA apply 和 Plan handoff。

Critic 首次准备请求时，执行评审中的用量观察也随 manifest 固定。同一操作因
暂停或派发延迟再次准备时，若候选、策略、任务图等非用量事实完全一致，则复用
原用量观察对应的不可变评审来源；新用量不会改变旧请求摘要。其他事实改变仍走
request divergence 拒绝，新操作重新读取当前用量。原 manifest/评审摘要均需
校验；这不是预算豁免，也不修改用量账本。入口及篡改回归：
`tests/test_plan_review_replay.py`。

同一 Plan 被新 fanout 替代后，reader 回收包括子角色及 aggregate 的 synth。
旧 synth operation 通过原有取消服务终止；provider 只有在其最新派发仍对应
旧 attempt 时才停止，确认退出后才释放角色。后续同角色新派发不受旧取消影响。
请求、读取账本与已有审查结果保留，取消不构成 Critic 通过。
回归：`tests/test_superseded_synth_closeout.py` 覆盖 Orchestrator sweep、
重复恢复、当前操作保留和角色复用。

## 局部重规划的独立 Critic 输入

Planner 可在明确 `task_ids` 的受控 replan 中交付 `scoped_plan_review.json`，
声明受影响任务、旧新 Task Map、Plan Package 和可分区补充规格的摘要及来源；
合同见 `zf-plan-task-map-contract`。这份声明是本次 Critic 的必读输入，不能
代替其独立 verdict、原始需求、完整候选 Task Map、固定矩阵或最终产品验收。

`prepare_call_operation` 只在同 Run 当前已准入 Package、前次独立 Critic 的
已准入结果、完整读取策略和绑定同一 Provider session 的完整 transcript 交付
证据、原需求摘要、当前 kernel trigger 和 fanout 身份均匹配时尝试选择局部读取。
交付观察只证明历史输出，不证明模型理解或当前记忆；目前仅已有 Codex transcript
观察能力，Claude 和无可核验交付证据的历史审查回退完整读取。Task Map 的全局部分、未声明任务、任务集合、非选中
Plan 端口和选中端口的 shared/未影响分区必须逐值或逐摘要一致；从新旧依赖图
闭包加入前置及下游任务，并把旧 Task Map 对应任务、旧新补充规格 shared 和
相应分区写入新的不可变读取策略。原完整来源仍可由 Critic 按需展开。显式
要求完整读取始终优先；缺失、歧义、全局变化、已失效 Package、先前只有局部
读取或任何校验失败，原必读清单原样保留。

同一 operation 重放若再次携带此前派发投影的 `required_reads`，先对照不可变
request 和读取策略；仅完全一致且确认为已生成的局部策略时，才恢复为原始输入
重新派生，避免误把派发投影当作新的显式读取要求。新的显式要求保留，并继续经过
既有 request divergence 检查。

局部声明中的任务必须与原 kernel replan 触发事件的非空 `task_ids` 完全一致；
这些 ID 沿既有任务图发布路径选择实施任务，其他已完成任务及证据不由此重开。
范围声明、读取收据及新 verdict 分别留痕，机械一致性不判断业务语义，也不复用
旧 verdict。真实派发入口、缺页准入、旧证据和完整读取回退见
`tests/test_plan_local_review.py`；任务保留及 writer 入口见
`tests/test_writer_fanout_runtime.py::test_scoped_replan_preserves_unselected_completed_task_evidence`。

局部审查结果进入 Plan Package 前，还必须绑定已准入的当前 Critic 结果和派发的
operation；当前基线、需求、最终 Task Map、Plan 端口和计划正文与受审候选不一致
时拒绝准入。该校验不会替代 Critic 的独立语义判断。

## 跨计划版本的受控差量审查

在有当前已准入 Plan Package 的受控 replan 中，`prepare_call_operation` 在发布
source manifest 和读取策略前尝试生成 `unchanged-plan-review.v1` 证据。它绑定旧包、
旧独立 Critic 的已准入结论与完整 Provider 观察、当前触发事件、当前 Task Map、
Planner 结果以及逐端口的新旧摘要。只有同一 Run、同一 Critic 角色、未修改的权威
需求、合法的 kernel 触发或可核实的受控 replan 动作链满足条件时才尝试；候选及
旧包取自现有不可变 sidecar。不能由 provider session 的存在推定当前记忆。

已完整见证的逐端口相同内容不再自动注入；有完整旧见证的 JSON 端口可在原始文件
摘要验证、拒绝重复键和非有限数、完整结构差异可重放为当前值、差异比正文小且
证据预算内时，以完整旧值/新值差异替代整份必读。差异不推断语义等价：新 Critic
必须自行审查全部变更、原始要求、当前 Task Map、共享义务及其相互影响，必要时
按原 capability 回读未变端口的原文。任务 Map、当前需求、Planner 报告和没有充分
证据或差异过大的材料继续完整读取；文本材料保持原政策。明确指定的必读优先，
旧 attempt 不被修改。内容去重不合并来源权限。
派发事件的 `plan_review_optimization` 提供当前策略、粗粒度回退原因、必读项数量、
去重选择字节和未知项数；缺失的大小标记 unknown，不能视为零或 Provider token。

差量证据进入新 plan-synth contract 和新 attempt 的不可变 manifest，证据本身为
必读；生成失败或身份、旧证据、摘要、范围不一致时维持原完整策略。结果准入继续
绑定本次 operation、来源、证据及精确候选，不能借旧结论自动批准新版本。会话轮换
不使历史 Provider 观察变成当前记忆；当前仅支持可核验的 Codex 历史见证，其他
Provider 按完整策略处理。`context_delivery` 的 Provider session 差量仍为 shadow，
本机制不改变该模式，不声称当前 Provider 必然保留旧正文。

针对 LS 停止状态的隔离副本，使用真实旧事件、已封存账本、来源和本次候选重建：
12 个端口摘要相同、4 个 JSON 端口可重放；以原有快照去重估算必读正文从
14,232,866 bytes 变为 3,573,707 bytes，另有 135,787 bytes 的证据 sidecar。
这不计选择渲染和工具封装，也不是 token、质量、账单或真实 Provider 运行证明。
实际使用前仍须核对原始身份/设计合同及跨任务要求；不因此更改任何进行中的策略。
