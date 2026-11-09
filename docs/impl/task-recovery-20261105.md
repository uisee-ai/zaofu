# Task Pipeline 断点修复（2026-11-05）

> 2026-11-09 合并更新：旧 shared-bodies/output-file 读取入口由 GitHub 的有界分页契约接替，详见 [合并与恢复记录](github-datal-recovery-20261109.md)。以下保留原修复历史。

本批任务见 `tasks/done/2026-11-05-1400-task-recovery.md`。

## 修复边界

失败分类的环境启发式只读取失败诊断字段和未通过探针的诊断，不能从已通过命令、文件路径、传输参数中推断故障。无结构化结论的 dev.blocked 继续交给语义 triage，不由内核臆测产品结论。真实连接错误仍保持环境恢复路线。

TaskRefManager 使用 `git diff --name-only -z` 读取原始文件名，按 NUL 分隔。中文、引号、换行路径仍逐项执行原有 scope 检查，不改变越界拒绝条件。

operation 已终结且任务 active_dispatch_id 清空时，旧 assigned_to 不能构成 worker 存活义务。仍核对最新 task.dispatched 的 operation、dispatch、stage 和 generation；新 operation/legacy dispatch 不被旧终局遮蔽。此一行修正在 dev 工作区此前存在，本批纳入独立 RED/GREEN 验证，不复制其余未提交修改。

## 验证与恢复

新增集成回归在修改前 13 failed / 3 passed；修复后与 TaskRef、生命周期、triage 直接调用测试共 107 passed。最终自动验证见任务归档记录。

恢复前确认无活跃 provider operation，保留工作区和审计账本；加载修复代码重启 runtime，让已有 operator-intervention 路线解除 remediation safe-halt。P04 明确批准一次额外代次，其他任务保持原有重试上限；所有结果继续走独立 Verify 和候选集成门禁。不得把恢复派发等同于产品完成。

## 产品修复责任

- P04：服务端写入/审核权限、worker host 签名和持久化、七字段查询及历史审核 UI、单位与条件校验、分类名/ID 规范化。
- P06：取消任意模型名生成固定几何的成功路径；注册 adapter/输入加载/执行回执。无已批准真实模型时保持人工待处理，不签收真实推理验收。
- P18：PAUSE_REQUESTED 允许当前有效 attempt 报告失败；取消只终结当前 attempt，历史记录逐字段保留。
- P16B：原隐私任务缺少 schedule 私有中间结果和发布/读取守卫接口，先由受控 replan 明确上游契约，再实现脱敏、审核、导出与撤权。普通内部 copy 不被追溯判定为隐私实现失败。

## Reader stage 重试的引用一致性

P16B 的历史 Task 派发包与当前 Run Plan 包不同，属于 gap-only 多代任务共存。首次操作将任务包作为 Run 规划包，被 preregistration 正确拒绝，未执行 reader。已纠正此恢复请求；没有放宽包准入规则。

后续发现 `stage_failure_replan` 仅更新 Plan 包/generation 却保留旧 task_map/source_index。修复从当前已接纳包的 source_event_id 同步读取 map/index 和对应摘要；不存在的新摘要不会继承旧值，未接纳草稿不构成来源。回归先 RED 后 GREEN；104 项 stage/run-manager/controlled-action 测试通过。任务见 `tasks/done/2026-11-05-1410-stage-replan-map-binding.md`。

当前全量 Plan 已有 P16B-RELEASE-CONTRACT 和 P16B-PRIVACY-R1，但 canonical 队列没有这两项且旧 P16B 仍被下游引用。受控规划需对账任务创建、替代和下游依赖，不能重复生成未物化的空 gap 集，也不能把不存在于当前 Map 的旧 ID 当作直接 supersede 目标。

## Discovery 替代图优先级

Reader 已声明新任务的 `supersedes_task_ids` 时，flow discovery bridge 使用该显式替代图；显式空数组表示追加，不再混入恢复请求继承的历史 ID。没有任务级声明的旧结果仍使用 envelope。任务是否存在、基线是否合法、图是否闭合继续由原准入检查负责；本修复不把历史节点自动签认为当前节点。实证为 `evt-4e10a21d6f6e`，修复回归涵盖当前节点、追加、兼容路线和未知节点拒绝。

## Candidate replan 使用同一引用规则

`replan_resynth` 与 reader stage retry 均调用 `plan_source_binding.bind_admitted_plan_source_refs`，从当前已接纳 Plan Package 的 source event 绑定 map/index 的 ref 与 digest。避免重合成沿用最早 scan/Planner trigger 的旧路径。只读回归覆盖两个实际入口；未接纳草稿、缺失新摘要及显式空 index 保持既定约束。

## 历史替代的准入对账

writer adoption 在物化本轮后继后，通过 `task_map_supersession_history` 读取当前 Map 的有限 amendment 引用链。只有同 workflow run 的 kernel `task_map.amended`、匹配的历史文件 SHA256、显式替代边和已经物化的当前后继共同成立，才补入历史替代。缺失/损坏引用、无事件、跨 run、无法闭合的后继链均不授权取消；仍有活跃派发的旧任务也保持原状。

随后复用原有 TaskStore / task_doc / EventWriter 路径：先更新保留任务依赖，再取消旧节点。取消事件记录历史证据 ID，并绑定当前 workflow run，供 Task Pipeline 的 supersession/归档检查消费。不读取 Planner 自由文字作为控制指令，不因旧 ID 不在新图就推断可删除，也不重开 done。

历史节点已经 cancelled 时，仍可据同样证明迁移滞留依赖，并补齐当前 run/Map 的替代见证；同图重放不重复追加见证，不修改 cancelled 状态。

## PAS 打包后身份字段的输入范围

Plan Package 会给原生 product_acceptance_spec.v1 绑定 schema/run/flow/revision/generation，而生产者分区基于绑定前正文。输入范围计算将这五个具名标量身份作为机械共享字段读取；原始语义路径仍按显式 task_input_policy 检查完整覆盖。其他 schema、对象型身份或遗漏的业务内容继续拒绝。读取侧不重写不可变工件、摘要、已批准 Plan 或已有 TaskAttempt，既有包无需重新生成即可使用同一已批准内容。

本修复接纳主工作区已运行的显式 task_input_policy 读取支持及其原有测试作为直接依赖，未接纳其余未提交改动。新增回归走实际 prepare_plan_artifact_package -> current_plan_port_sources，修改前 1 failed / 12 passed。

## 监工控制面事件窗口开销

`EventLog.read_days` 保留原有归档窗口选择，按文件身份、大小/时间和签名策略缓存完整行，并只解码追加尾部。轮转、删除、截断或同大小重写失效；半行不前进。缓存最多16个文件/验证策略组合，仅供读取，不影响 EventWriter、事件顺序或规范状态。实况生命周期usage扫描反复解析112MB日志，控制进程心跳可用但恢复tick落后，需以真正派发和tick恢复验证效果。

Task Pipeline 静默门禁同时识别 kernel task.dispatched/task.done、绑定 operation 与 control_result_ref 的实现/验证结果，以及非终局 task-stage operation。失败只唤醒恢复，不等于通过、自动消除升级或扩大重试配额；心跳与无绑定worker自报仍不唤醒。旧P07B孤儿升级导致后续P16B结果未触发RM，已新增真实tick_services入口回归。

## 2026-11-06：恢复引用必须取已接纳包端口

`fanout.synth.completed` 的正文可以仅通过 `control_result_ref` 存储。恢复事件不能假设源事件顶层含最新 `task_map_ref`，否则当前 package 身份会搭配旧图。reader replan 与 candidate resynth 现在经配置 `state_dir` 验证当前 immutable package，检查运行/代次，并从 produced/inherited 中取 task_map/source_index 及摘要。缺失或损坏拒绝恢复；当前包无可选 source_index 时移除旧索引。纯事件投影调用保留旧兼容路径，真实 runtime caller 必须传入state_dir。

本次不修改已经派发的 Planner 输入或包，不将运行时补丁等同于产品验收。详见任务 `2026-11-06-0005-replan-package-port-binding.md` 与持续 active 产品恢复记录。

## 2026-11-06：增量派发保留新前置

候选修订保留 `failed_task_ids` 与 `downstream_task_ids` 作为上一代诊断。对于明确的 `task-map-amend.v1 / gap_tasks_only`，只有触发选择精确等于该诊断并且失败集合与声明被替代集合相同，writer 才改用该计划批准的 `amend.gap_task_ids`。这样不会漏掉新公共前置，也不会把未修订下游重新纳入本次续作。明确的新任务子集及无关选择沿用原逻辑，声明任务缺失仍由 admission 拒绝。

本轮草稿实物验证从错误17项收敛为5项：align加载前置→采样/融合；资源读取守卫→隐私。尚需独立Critic与计划批准；这不是任务实现或产品验收证据。记录：`2026-11-06-0015-gap-diagnostic-selection.md`。

## 2026-11-06 增量替代包含下游

恢复事件中的 failed_task_ids / downstream_task_ids 是上一代诊断范围。若显式
`task-map-amend.v1` 声明 `gap_tasks_only`，且请求集合精确等于诊断并集，选择器仅在
`failed ⊆ superseded ⊆ diagnosis` 时使用批准的 `gap_task_ids`。这允许替代一个依赖
发生变化的下游，但不允许包含无关任务或遗漏失败任务。显式新任务子集仍保留。
其余 schema、路径、替代链、矩阵、审批和任务级验收继续按既有入口执行。

真实故障 evt-55bf5d9b6b6a：三个失败、一个下游被替代，却因旧等值检查而保留十八项
诊断选择，漏掉唯一 PostgreSQL 前置。现有 gap-only 准入分支因此未生效，错误报根
脚手架无 owner。修复选择边界即可；无需扩大已完成 F00 的写域或修改计划字节。
对应任务：`tasks/done/2026-11-06-0645-gap-downstream-selection.md`。

## 2026-11-07：Gap 准入拒绝的恢复闭环

`flow.gap_plan.ready` 只是增量提案，不代表 `task_map.amended` 已接纳。此前 reader
恢复看到该提案即认定 `handled_by_gap_plan`，下游准入失败没有立即路由回生产者，
实际出现留存 owner 冲突与对齐真值缺 rolling-smoke 两次停滞。

恢复现在沿 `task_map.amend.failed -> gap source_event_id -> reader failure` 的明确
因果链复用既有 stage retry。反馈携带原 gap 和准入拒绝事件引用，语义内容仍由 reader
修订；没有扩大写域、删除条款或放宽 cap。额度耗尽复用既有升级路径，不能等 orphan
计时器后才报告卡点。Run Manager projection/apply 同样可以消费有额度的修复。

旧 amend 失败可只带 child trace；在明确 source_event_id 链下核对显式 canonical
run 冲突，不能将 child trace 与 workflow ID 不等误当跨 run。待准入或已成功的 gap
仍保留原恢复所有权。后续明确引用这次失败的 operator/planner 接管会抑制旧恢复；
同 run 无关的新计划或只有 package supersedes 关系不足以宣称缺口已解决。

实况追踪见 `tasks/active/2026-11-07-0200-two-gap-supervision.md`。本轮修复不把重新
派发、环境连通或已提交局部代码等同产品完成；视频、留存和数据集仍须独立验收。

## 2026-11-07：重复激活不移动 reader 恢复代次

生命周期查询将 `run.goal.blocked -> active` 作为该阻塞代次的恢复边界。过去每个
后续 `active/running` 更新都会覆盖这个边界，令中间派发但仍在运行的 reader 变成
“旧代次已终结”。当父 Task 为 backlog 时，reader 又被当作 idle，72%上下文触发
fresh recycle，尚未落盘的规划丢失。

`lifecycle_evidence_queries._terminal_run_scope` 现在只登记同一阻塞代次的首次
恢复。重复激活幂等；新blocked代次仍要求新的恢复，completed/cancelled等硬终局仍
不被active复活。真实context threshold与pending recycle入口、跨角色查询、reader
正常终结后回收均有覆盖。没有修改上下文阈值或禁用hard-cap。

固定历史窗口 `evt-7932592c37db` 之前，旧查询为active_child=null/terminal，修复后
识别原派发 `evt-b0f3d36451ca` 为active。证据与验证记录见
`tasks/done/2026-11-07-0216-reader-activation-epoch.md`。

## 2026-11-07：Broad resume 纳入明确批准的新前置

当 amendment 与有效恢复 scope 都明确为 `failed_children_and_downstream`，且触发
选择精确等于旧 `failed ∪ downstream` 诊断，并满足
`failed ⊆ superseded ⊆ diagnosis`，writer 将 `amend.gap_task_ids` 并入原选择。
随后仍由既有 canonical successor 映射、缺失ID/基线与完整 pipeline 门禁校验。
这修复“一对多替代只选到末端、遗漏新增前置”的机械缺口，不新增语义依赖推断。

`gap_tasks_only` 仍只选批准的gap。显式新任务子集、无关选择、scope不匹配或无法
证明的替代关系不扩展；broad恢复不借gap-only分支跳过完整图的根owner检查。
本轮固定图原选13项，正确并集应为16项，新增3前置由DAG调度而非强制同时开跑。
任务与验证记录：`tasks/done/2026-11-07-0237-broad-gap-selection.md`。

## Task Pipeline context 回收归属（2026-11-07）

`worker.context.critical` 是 provider 生命周期事实，不是未完成 Impl/Verify 的
语义失败。`task_pipeline_context_recovery` 在原事件的 ledger 位置核对 running
operation 的 task/run/dispatch/role 和 kernel 来源，再复用 reactor 的既有 ownership
guard，避免完成审计先清除当前 Task/attempt。由该 critical 明确派生的旧 retry 或
continuation schedule 同样不能重开后续代次。未知来源、跨 run、其他 stage 和 timeout
继续原路径；reader guard 保持原判定。

上下文阈值、hold 和 fresh recycle 不变。冷 runtime 的高水位样本仍进入 pending
recycle，hold 到期仍通过原 lifecycle 回收和恢复 briefing，保留 operation、attempt、
lease 和 dispatch 身份。本片不自动 redrive，不改变重试预算或 result admission。
历史上已经被审计挂起的 operation 仍应使用精确 `recover workflow` checkpoint。
任务与证据见 [上下文回收归属修复](../../tasks/done/2026-11-07-0404-pipeline-context-ownership.md)。

## 无 Task 的 gap 准入失败入口（2026-11-07）

`task_map.amend.failed` 的 builtin 需要同时进入 wake patterns 和 Kernel liveness
集合。仅在 registry 注册不足以覆盖真实调用：事件没有 task_id 时，Layer 1 的普通
分支跳过 builtin，Layer 2 配置则将其交给 agent。现明确该事件仍是机械准入/恢复事实，
两种模式都调用既有 gap 因果解析及 bounded reader replan；不改变 cap。

回归从 EventWatcher 到 CLI batch、真实 `Orchestrator.run_once`，覆盖有额度/耗尽、
Layer 1/Layer 2 和重复输入，不再只直接调用 handler。对本轮旧故障，源码补丁不代表
自动重放已消费的失败；应由 operator 基于明确 source/checkpoint 做一次受控恢复。
历史 reader cap 耗尽时 `stage-replan-new-generation` 需要真实 cap escalation；本次旧
入口遗漏未产生该事件，不能伪造。已有 owner 授权可经 `candidate-rework-apply/replan`
把已确认的两个 gap 及拒绝原因交给 Planner，限定修订范围，并保留接纳时全部 done（本轮最新29项）、当前
候选和 Dataset 未准入实现作为复用来源，不把后者当作通过验收的基线。
任务记录：[Gap 准入入口闭环](../../tasks/done/2026-11-07-1046-gap-admission-entrypoint.md)。

## Autoresearch 执行进程互斥（2026-11-07）

sidecar 启动锁由父 watcher 持有；父进程退出而 resident 存活时，启动锁不能证明
消费者唯一。`autoresearch resident --execute` 因此在实际 CLI 进程内，对配置的
state_dir 持有 `processes/autoresearch-resident-execution.lock` 非阻塞文件锁，覆盖
单次执行或整个 watch 生命周期。同 state 的第二个执行 CLI 在读取/接纳动作前返回
非零并报告 already running；只读预览和其它 state 不受影响。

锁文件不删除，避免并发调用者锁定不同 inode；描述符不传给子命令，正常退出或进程
崩溃由 OS 释放。原 SIGTERM 当前 tick 收尾语义保持。父侧启动协调继续保留，不能
把旧进程的存活或一条 started 当作成功诊断。既有旧版 resident 不会热加载该保护，
部署时先精确清理无子任务的重复旧进程，再在活动诊断安全结束后重启登记 resident。
本次任务：[执行进程互斥修复](../../tasks/done/2026-11-07-1524-autoresearch-resident-process-lock.md)。

### 2026-11-07：必读正文无损共享（可选 CLI 呈现）

媒体独立 Verify 的必读结果包含大量跨文件重复子树；旧逐文件去重仍产生工具输出截断。
`zf artifact read --attempt <attempt> --required --shared-bodies` 在正常逐来源授权、摘要校验和
read-ledger 写入之后，输出 JSONL 的 `body` 定义与 `source` 来源/收据。
重复正文只呈现一次；定义中的 `refs` 使用类型保真的数组路径指向此前定义，替换对应占位 null
即可还原原值。来源的 `content_sha256` 对应原始 `_encode_selected` 正文，字段顺序、Unicode、
纯文本及字面 null 不变。引用字段位于正文之外，业务数据中的同名字段不会被解释成引用。

此选项不修改必读范围、冻结 manifest、准入 gate 或证明强度；不与 `--deduplicate-content`
组合。CLI 在本次调用内收集已授权正文，任何来源失败均不输出共享展示，但保留真实读取收据。
旧格式保持兼容。worker 应将输出保存在自身工作目录，分段消费全部定义与来源，确认工具输出
没有截断；仅看到引用、摘要或 CLI 成功不等于已经阅读正文，亦不能替代独立验收。

修复记录：[无损必读正文共享](../../tasks/done/2026-11-07-1543-lossless-required-read-bodies.md)。

### 2026-11-07：Verify 弃权与产品拒绝分离

`verification-result.v1` 的 `execution_status=failed/verdict=abstained` 表示验证执行失败，
没有产品否定结论。Task Pipeline 对 settled 弃权保持 blocked，并投影
`verify_execution_failed`；不自动派发 Impl，不增加产品语义返工，也不报告
`semantic_rework_exhausted` 或 verify-only 产品拒绝。真正 rejected 的原返工路径不变。
恢复仍须先排除执行条件问题，再走受控恢复；此补丁没有添加无限自动重试、改写历史事件或
给已派发 worker 追溯撤销。现场已误派的媒体 Impl 保留原 HEAD，并重用仍有效的 exact-head
证据，再进入独立 Verify，绝不把弃权视为通过。

记录：[验证弃权分流](../../tasks/done/2026-11-07-1558-verifier-abstention-routing.md)。

### 上下文回收的现场配置边界

本轮媒体worker在结果草稿已写好、尚未submit时，因ratio>0.9持续180秒被强制回收，
新会话必须重新消费完整输入。运行配置已对PRD的16个Impl/Verify lane将
`drain_hold_seconds`设为600秒，保留原context阈值、backend、权限、并行容量和验收合同。
这是有界操作配置，不是取消上下文保护或token预算。覆盖角色时必须显式保留原backend，
并对编译后的完整RoleConfig做逐字段比较；仅设置Flow roleDefaults可能不会覆盖已生成lane。
产品共享checkout的原配置差异未被本修复提交收编；确切前后文本与摘要在监工诊断目录保留。

配置与回收语义验证68项通过；真实回收观察、后续独立验证和最终任务完成仍由监工继续追踪。

17:22 UTC现场闭环：Core Audit经独立Verify新增7项探针及真实健康事务窗口验证后通过，
内核集成到candidate `ee2e1d5`并自动派发Dataset R3。媒体Verify在原身份完成798672字符、
641正文/244来源的无损输入消费与摘要校验，继续独立验证。两Verify正常compact后续读，
未再次强制回收。有效任务完成31/45，不能将输入恢复或Core局部通过等同于整体完成。
操作记录见[持续监工](../../tasks/active/2026-11-07-1220-live-privacy-lifecycle-supervision.md)。

### 已验证任务在集成失败后的代码修复

`zf recover task-integration --operation <id> --reason <原因>` 仍只预演同提交的集成重试。
若现场证据要求修复允许范围内的代码或测试夹具，使用
`--rework-impl --approval-ref <审批证据>` 预演，再加 `--apply` 请求下一代 Impl。
入口要求当前 blocked integration 的 canonical needs_review、同代次同提交的已准入
Impl/Verify 通过结果、活动 run/task、无活动 attempt 或继任 operation。缺一项即拒绝。

恢复只追加 `task.rework.requested`，不改写历史 Verify、TaskRef 或 candidate。
审批只授权下一代；新 Impl 后必须再独立 Verify、再集成。旧请求不能重放到新代次，
默认返工上限不改变。环境失败而无需改源码时应继续使用原集成重试模式。
记录：[集成失败有界恢复](../../tasks/done/2026-11-07-1742-reviewed-integration-impl-rework.md)。

## Consumer 锁竞争与真实执行超时

Workflow Synthesis 的 Web preparation host 和 runtime tick 共用consumer lease。
获取该lease超时使用 `WorkflowSynthesisConsumerBusy`（TimeoutError子类）表达另一host
正在处理；runtime本轮延后，Web不将当前fingerprint标为已消费。lease内的事件读取、
处理或provider错误仍按原失败路径报告，不能按异常基类吞掉全部TimeoutError。
回归与现场记录：[consumer锁竞争](../../tasks/done/2026-11-07-1935-synthesis-consumer-lease-contention.md)。

## 尚未派发的 Task Pipeline 请求恢复

`zf recover workflow` 对只有 planned attempt 身份、但没有真实派发记录的 requested
operation 返回 `no_action`，交给调度器继续等待；不能因为没有活跃 attempt 就将它当
过期 worker 取消。对于历史恢复工具以 `workflow_resume_cancelled_stale_operation`
取消的同类请求，提供 `redrive_undispatched_task_pipeline_operation` 检查点。

投影、应用及 operation service 均校验没有 started/dispatch/result 身份、没有任何历史
TaskAttempt（包括 expired）、事件账本没有对应 attempt/dispatch。状态文件损坏时拒绝。
应用时再次检查当前状态与历史证据，service 要求同 run、operation、request hash 的
`workflow.resume.planned` 决定；只接受 kernel replay。恢复保留原请求摘要与代次，通过
既有 redrive 事件回到 requested，由正常调度器创建实际 attempt。手动取消、已启动、
已有结果及已有历史派发的 operation 不在此恢复范围，不直接修改 TaskStore。

验证见 `tests/test_undispatched_pipeline_recovery.py`，现场持续记录见
[监工记录](../../tasks/active/2026-11-07-1220-live-privacy-lifecycle-supervision.md)。

## 已准入结果后的恢复边界（2026-11-08）

Task Pipeline 最新派发已有已准入 settled 结果时，通用 orphan 检测停止计时；blocked
与abstained仍由各自恢复owner负责，不冒认完成。新派发不能继承旧结果存活证明。
人工批准Plan或受控task-impl重试可唤醒静默；未知worker噪声不会唤醒。原静默保护保留。

`zf recover workflow --dry-run --json` 对已准入 failed/abstained Verify 提供
`redrive_abstained_task_verification`。按checkpoint-id应用后，保留原operation、请求摘要、
产品代次、target与历史账本，清除当前结果投影并由Kernel派发新attempt；每operation仅
一次，不增加产品返工预算。须存在同代已通过Impl、相同目标及验证失败来源，禁止恢复
产品rejected/passed、任务已终结、run终结、新代次或已有活跃attempt。重复失败需再诊断，
不得无限重试；旧attempt结果仍由现有准入检查拒绝。

`zf artifact read --attempt <id> --required --shared-bodies --output-file
app/evidence/<domain>/__pycache__/required-<id>.jsonl` 将原有受控读取直接原子保存，
stdout仅包含路径/大小/hash。仅允许当前目录内gitignored缓存且不可覆盖；全部来源成功
后才发布缓存，失败保留真实ledger且不发布半成品。缓存回执不证明模型已读；必须继续
完整分页，不能因未读完就自行结束。普通stdout接口兼容。

Run Manager semantic-replan-request的效果是绑定同request_id/run的任务图产出或无gap
Discovery完成，不是task.done。Discovery发现产品缺口且有gap时等待物化；没有gap的
执行失败仍失败，同run其他attempt失败不结算此请求。计划审批、实现和最终验收继续
经过各自gate。现场与验收记录见
[恢复批次](../../tasks/active/2026-11-08-0617-settled-recovery-closeout.md)。

### 2026-11-08 06:43 UTC 现场恢复验证

实现提交 `bf758afe`，共享主树以唯一前像补丁部署，主环境77项定向测试通过。控制面重载为PID1501615/epoch dc24d6dc203345c6a2111926e3028133，未关闭worker/resident；产品两份角色技能源也精确更新，当前attempt通过补充指令使用新入口。

- Training原固定提交`b12222f3`的Verify保留operation/hash/generation，通过`op-resume-ff05716ab6e47f293a882b7b`受控续验；新attempt `ta-f922613c715d84614250`，实际dispatch `evt-58ee7aaae4cb`，已成功原子捕获2,036,777字节必读缓存并开始连续分页。缓存回执明确不证明模型已消费。
- Consumer Waits受控Impl g2请求`evt-6bd83f920d5e`，新attempt `ta-cfc82ecbd39f6b42d527`、dispatch `evt-3de8a47ad467`；实际在读本次身份资料并开始分页，原五域测试写域和业务断言保持。
- `run.goal.quiescent.exited`=`evt-9ec97c26b8f5`，不是仅修改UI状态。35 done不变。
- 独立复核b6c9d8候选，机械跨矩阵校验确有102错误、execution_spec缺9个当前任务原生正文；拒绝`evt-692a32048f10`，没有误操作Oct28旧pending。自动恢复触发历史累计第5次cap `evt-7a30af2f5d67`。基于用户已授权修复及实际复现，受控提交一次有界计划一致性修订：`evt-37810cdc3635`→`evt-8487e647691f`/`evt-0a2d5221aa8f`，保留原计数/全局上限和35 done、两在途快照；Planner已收到新briefing。修订仍需Critic/监工审查后才可批准，Ingest锁序产品修复尚未完成。

现场完整证据在产品根`diagnostics/settled-recovery-20261108/`：部署前像/补丁/摘要，77项主树复验日志位于`/tmp/zf-supervision-20261107/settled-main-validation.log`；恢复结果、独立计划校验、受控修订payload和protected_before、latest.json共同保留恢复因果。所有历史失败保持原判；尚无新的产品验收通过，持续监工项保持active。

### 2026-11-08 07:05 UTC 持续监工环境恢复

Consumer已消费必读进入基线，但av16.0.1/Pillow12.0.0的PyPI下载再次超过bootstrap 300秒；这与此前媒体/Privacy/Dataset环境问题同源，不能记作产品RED。原锁哈希逐项匹配产品持久wheelhouse `diagnostics/media-codec-wheelhouse-20261107/`，监工在独立临时Python3.11环境验证原锁+`--require-hashes`+本地file URI约束，29包offline安装成功。临时环境已清理，worker venv未由监工并发修改，已指导worker释放自身旧bootstrap锁后按原验证命令重跑。约束及完整日志保存于`diagnostics/settled-recovery-20261108/{locked-local-media.txt,consumer-dependency-recovery.json,consumer-offline-environment-proof.log,consumer-bootstrap-guidance.json}`。现有依赖补给方式未自动传播至新worker，后续启动前应核对并复用已验证环境事实；本次不硬编码主机路径为产品默认值。

本批验证日志/选取器规划/RED证据已从临时目录复制至产品持久目录`diagnostics/settled-recovery-20261108/verification/`并记录SHA256索引；当前恢复和计划修订尚在运行，未追加产品done。

### Verify恢复的当前代次围栏补充

复核R1发现：只拒绝更高operation_generation不足以覆盖“新Plan已接管但新operation尚未创建”的窗口。旧代码在task_map_generation变化及package digest变化两个回归场景均仍列出旧Verify恢复点（2项RED）；现要求存在本任务当前generation准入，run/map generation/package digest与旧操作全部一致才允许续验。相关模块/恢复入口/Task Pipeline调用方84 passed，sentinel 50 passed/1 skipped，runtime-noise mock E2E 3 passed。不修改当前在途Training/Consumer/Planner的合同、attempt或读取规则。

### 2026-11-08 07:38 UTC：Planner 大端口提交格式

现场复现：briefing只展示内联body，Planner先用artifact_ref提交PAS/execution_spec未被识别，再内联完整正文触发1MiB结果限制。现有运行时已支持工作目录相对ref+原文件sha256，问题是指导遗漏而非限制不足。已发送精确协议指导，Planner保留完整正文，将9端口描述符结果降至6945字节；继续等待正式校验与独立Critic，不宣称计划批准。将同一说明补入生成briefing的plan_port_briefing.py；直接调用方及预检/交接42项测试通过，不改变schema、读域或大小限制。

控制面07:29重载后，watcher epoch为7391c91e29c24a4aa44eac0c2916a118；Training Verify、Consumer Impl、Planner三operation及attempt未变，quiescent=false。Consumer当前提交9504a88，同HEAD全量复验仍在执行；完成数仍35。

07:42 UTC：独立Critic已派发。其仍受共用briefing的stdout首读建议影响，选用了非安全缓存目录而被CLI正确拒绝；已指导使用工作区内artifacts/plan/__pycache__。将完整缓存命令及路径约束补入所有角色共用Controlled Artifact Inputs提示，避免只更新三个角色技能而漏掉Critic。只修改提示，不改ledger/鉴权/读取语义；相关ledger、batch CLI、原子文件输出及fanout调用方测试41 passed。

### 2026-11-08 07:44 UTC：原生压缩与强制回收竞态

事件evt-863884b80408/evt-5bc8f7c75dff显示Consumer在context ratio 0.94超过600秒后被强制回收；监工此前连续观察到Compacting context，原生压缩尚未完成。采样确实越过阈值，不能把它误称纯陈旧采样。代码缺少在回收边界给已启动原生压缩有界完成机会。工作区/9504a88及所有验证证据保留，原operation/attempt不变，新pane %781已收到recovery briefing和精确断点补充。

新增native_compaction_recycle.py并接入_check_context_thresholds实际force分支：只对可识别Codex当前状态行，按session+高水位周期给一次drain_hold_seconds宽限；不因重复观察续期。压缩状态消失后暂保留该界限等待新usage；低水位沿既有规则解除hard-cap标记。历史状态/普通Working/不可读pane不延后，过期仍回收；不发送虚假compacted事件。复现旧实现2 failed/5 passed；修复生命周期闭包91 passed，追加provider身份隔离8 passed；sentinel50 passed/1 skipped、相关mock E2E3 passed。未跑全pytest或真实provider能力测试。

Sources额外浏览器失败已持久记录consumer-extra-browser-failure.json：固定9504a88，120秒导入RUNNING超时，2pass/1fail；保留trace/WebM及非零收据，尚无足够证据归因与Ingest死锁相同。五域mandatory smoke/runtime及delta、自有74个旧失败用例复验通过，并非全产品浏览器通过。独立Verify与计划Critic仍待完成。

### 2026-11-08 08:16 UTC：续验接纳与集成环境修复

07:52以受控control-plane-only重载部署`a52b8c5f`，新watcher PID2029325/epoch a088ea1a6eca4e66b3e46afce7b90404；三在途operation及attempt逐项核对未变，主checkout定向41项通过。Consumer后续原生压缩正常完成，会话及断点保留；这次完成本身不证明曾触发新增宽限分支。

Training固定`b12222f3`的新Verify已被接纳：`evt-8231642c21ad`，settled=`evt-b43f787e93de`。独立探针覆盖真实暂停/恢复、指标去重、weights身份、比较及撤权全表/字节不变集；129张表、59个文件，拒绝路径只增加允许审计。4个本层AC通过，T14-075/076/077仍由最终candidate验收负责；旧abstained结果保留。忽略缓存内探针脚本/XML/观测结果已按SHA归档至产品`diagnostics/settled-recovery-20261108/training-independent-probe/`。

随后集成`VC-P10-smoke`失败，未增加done：新candidate checkout缺少显式`ADL_TRAINING_PYTHON`或`app/.cache/training-runner.json`，6项测试均因环境前置缺失报错。监工核实同一Python3.11.14/PyTorch2.5.1+cpu解释器及CPU梯度计算，配置candidate本地忽略测试文件；不修改产品默认、源码或断言。失败回执`c8f445434f2f8fc672974b5b192414b143be3ece787bf718002f7b8a500159d0.json`及环境修复回执保留。`zf recover task-integration`先preview再apply，受控请求`evt-ce26634bd468`；新generation2集成`wop-integration-tp-P10-TRAINING-R1-2999f8db06c36-9c4971d37b14`实际开始，等待原smoke结果，不能把环境配置当产品通过。

Planner上一版已消除102项跨矩阵错误并物化9个正文，但独立Critic发现两项高缺陷：F1全局替换污染历史parent/supersedes/固定ref选择器；F2当前D-REVIEW与集成契约仍残留45任务/298AC/旧gap审批范围。拒绝`evt-c8620a00f7d9`保留。根据用户修复授权，经既有stage-replan-new-generation受控动作形成`evt-7742570ba6e6`，只返修F1/F2，保护35done与在途任务、不提高全局cap；新Planner已派发。旧冻结包复制被写域hook拒绝，监工将17个文件18,751,243字节按源ref/SHA复制到其允许且忽略的本地输入缓存；worker逐字节核对后自行恢复输出并修订。没有改旧冻结工件或放宽hook，也没有把复制回执当已读或计划批准。过程记录位于同一diagnostics目录的plan-lineage-controlled-action.json、planner-prior-input-cache.json和planner-prior-input-guidance.json。

08:17 UTC重试结果：原Training smoke 6 passed/9 deselected，回执`17df91f13d75b829d47205e0449d64affd115c4202d7b7828c196cd3475d0e96.json`；集成回执`17c097c649aec0ad72b0ba3e6ed94efab2163dc00e20100b58ef93f24ce8c6a0.json`、`task.done=evt-e323cb625282`。candidate实际推进至`784a5de95b125b3c296cc45db1e46e4df5da2832`，36done。下游P11A模型仓库/谱系/回滚自动派发`evt-e04306b6ef61`，attempt `ta-0b4d5296c84e541a74f4`，与Consumer并行；监工提前供给原锁哈希wheel及显式测试解释器配置，未修改任务合同或产品源码。

新Planner提交`evt-ac37db233a5a`/准入`evt-5597aa6fcc3f`；冻结fanout 7742570b的独立差异核对306项无失败：Task记录、319AC/199命令、未变端口保持，历史选择器指向固定ref中真实存在的前身，当前D-REVIEW/集成契约及上游副本一致。此为差异复核而非重新完整产品验收；独立Critic `wop-prd-plan-synth-trig-evt-7742570b-c0c601218e0b`继续审查，尚未批准。已提供Training新done/P11A在途证据，审批必须保留冻结35done快照之后的正常进展。

### 2026-11-08 08:38 UTC：Consumer交接与压缩前置误报

Consumer在`9504a88`完成恢复读取，343个来源/856个共享正文核对；11条mandatory全部通过、full150/smoke6/旧失败重复74通过，146文件及历史归档成员哈希核验。实施结果`evt-9f6689001697`已准入，独立Verify操作`wop-verify-tp-P16B-CONSUMER-WAITS-R1-2a3175-339619ad819a`已派发，仍不能标任务done。额外Sources浏览器失败新归因：118次HTTP200查询持续推进，结束为RUNNING，注册73/发现201文件；没有当时PG阻塞采样，不足以证明死锁。失败trace/WebM不改判，证据副本`consumer-sources-progress-attribution.json`与SHA保存在产品diagnostics。

P11A运行中出现`evt-bc9d45e07e18`：context ratio0.7215，Codex能力标记compact_requires_idle，尚未发送compact命令，代码却发出compact.failed，Supervisor据此产生repair_harness注意项。这是前置不满足被记成执行失败；任务仍运行。修复将idle-only能力检查前置于attempt消耗，继续现有warning/pending_recycle路径，不伪造failed/requested/compacted。真正transport拒绝、异常和恢复失败仍走原failed，硬上限与R6有界宽限保持。

行为RED复现1 failed；修复后生命周期/阈值/硬上限/原生宽限/调用方及mock context-recycle E2E共81 passed，Supervisor注意项/事件合同27 passed，sentinel50 passed/1 skipped。未运行全pytest或真实provider能力测试；现场部署和后续观察仍单独记录。

R7提交`a5dd46a6`已精确部署，主checkout26 passed；受控重载`evt-8230d0663ab4`后watcher PID2271632/epoch 8f83d89d71204b50bab0666a542d6019，P11A、Consumer Verify、Critic三operation/attempt逐项保持。

### 2026-11-08 额度耗尽后的恢复误路由

后续真实provider拒绝：终端出现“You’ve hit your usage limit”，提示Nov 14th, 2026 10:14 PM重试；不是此前仅余量低的提醒。三个worker仍链接同一账号文件，未观察到账户配置更新；这不能证明是否另外充值，已询问用户，未假定额度已恢复。禁止为继续运行而擅自更换模型或修改本地预算。

现场静默恢复依次发出`evt-0de732f568a3`等worker.stuck，将P11A和Consumer从in_progress回写backlog并重启；provider readiness因同屏模型chooser被分类为provider_interactive_blocked。Critic attempt于08:59 lease_expired；Verify多次重启后产生respawn_success_circuit_opened、`evt-5b3579ebf9f0` dispatch.paused。当前36done、candidate784a5de9保留，P11A/Consumer工作树干净；三operation仍running而原attempt已失败，不能把界面running当实际执行。完整因果快照`diagnostics/settled-recovery-20261108/provider-quota-interruption.json`及工作树保存记录已落盘。

R8修复：硬限额优先于同屏chooser；旧限额/登录/chooser文本若已被后续真实Working/Compacting状态取代，不作为当前阻塞。共享live prompt观察先核实provider进程仍活着；中心stuck入口（覆盖pane/heartbeat）以及硬上限回收、idle drain、重启后孤儿回收在当前外部输入阻塞期间等待，不重排Task、不消耗respawn次数、不旋转provider session。既有Pane Probe继续将这类外部依赖通知owner；不伪造心跳、额度、任务通过或修改预算/lease到期规则。提示解除后原硬上限/回收检查恢复，dead/unreadable pane及普通真卡顿不获豁免；既有safe-halt不会被自动清除。

旧实现7场景RED；完整调用闭包146 passed，含真实Orchestrator的pane/heartbeat入口、context三条回收路径、提示解除后恢复与死进程负例、同一synth operation延迟后恢复、mock context-recycle E2E、Supervisor/事件合同。只测确定性与mock层，未消耗真实provider请求验证额度。仍需用户恢复额度，随后核对并通过受控恢复处理过期attempt与safe-halt，再观察新dispatch；本地修复不代表产品已续跑。


### 2026-11-08 09:47 UTC：控制面重载不能解除恢复门禁

R8提交`fd90498f`精确部署后主环境72 passed，重载`evt-a83ecf6d3cb1`将watcher更新为PID2560832/epoch ff2157eae7074ea4b37446fd796a6d01，三operation及attempt身份不变。但现场后验发现`evt-fd8c110e1146`自动dispatch.resumed：构造器未接收control-plane-only标志，将只重载控制面也当作解除safe-halt的人工重启。原重载意图声明保留暂停，实际未做到；保留此失败证据，不把重载成功等同恢复状态正确。

终端仍明确usage limit，36done不变、没有新的真实派发。监工通过EventWriter追加`evt-8351554be484`恢复operator暂停，关联原暂停和意外resume，不重写旧事件或任务状态。R9只把既有control_plane_only启动标志传到实际WorkflowRuntimeCoordinator构造入口：该模式保留blocked_human与dispatch暂停；正常完整启动仍沿既有恢复行为，维护暂停规则不变。CLI入口回归旧代码1 failed/1 passed，覆盖真实start参数解析、构造器和账本投影，而非仅测试孤立函数。

R9验证：实际CLI/start/restart、恢复级联、worker状态、外部限额保护、watcher暂停、事件合同及Task Pipeline runtime-noise mock E2E，10模块139 passed；sentinel50 passed/1 skipped。dev-verify针对三条改动路径建议因289文件阈值运行全套；按AGENTS按行为边界选择上述调用闭包，未执行全pytest或真实provider测试。


R9提交`d6e4486f fix: preserve recovery gates during control-plane reload`精确部署，主环境42 passed。受控重载`evt-4eee4f63ae93`后，09:51 UTC现场watcher PID2594113/epoch 2d8da519e7144b46ac108a4f5432b984；三operation及attempt身份逐项保持，最新派发事件仍为operator暂停`evt-8351554be484`，36done不变，Dashboard HTTP200。重载前后证据及部署前像保存在产品diagnostics/settled-recovery-20261108/reload-pause-*.json、verification/reload-pause-*.log。此为控制面修复验证；三个旧attempt失败/过期，产品没有实际续跑，仍待worker账号额度恢复后受控对账并继续，R5不标done。


### 2026-11-08 10:27 UTC：用户确认额度恢复后的真实续跑

用户明确确认Codex已重置且有额度，授权继续。执行正式`zf stop --fast`保留工作树/36done并产生可重放中断；随后分别用`recover workflow --resume-pending --checkpoint-id`恢复P11A与Consumer Verify，保留原operation/hash/产品代次，使用新attempt而非复用失败lease。恢复回执保存在产品diagnostics/settled-recovery-20261108/quota-restored-{p11a,consumer}-apply.json。

完整启动首次遇到flow_role_activation_config_drift。逐项复核：当前六个非lane角色本已匹配；16个Impl/Verify lane的drain_hold_seconds由原180改为600，恢复180后22个角色的整个role_config_digest与原准入manifest全部一致。有效配置快照缺少新backends默认字段并非本次阻塞原因（原manifest解析后摘要已经包含默认值），不据此放宽schema/digest校验。已备份zf.yaml并仅恢复16处临时回收等待值；8+8容量、模型、权限、预算、任务合同均未改，R6/R8压缩与外部阻塞保护仍保留。证据quota-restored-config-reconciliation.json含前后SHA及22项比对。

重启watcher PID2749529/epoch 5e07a5b1f09948fc9ba7f93632b822e8，operator恢复派发evt-7dfc2987270b。P11A于10:25真实派发impl-g1-p2（ta-08923d694f571fdb8185，pane %812）；Consumer在固定9504a88上派发verify-g2-p2（ta-f4f11258c899cd51438a，pane %811）。两终端均开始读取当前briefing/task.md并持续执行，额度恢复有新turn工具调用证据，不能仅凭历史usage-limit文本认为仍无额度。当前TaskStore为36done、2in_progress、13backlog（含父任务）；有效50项中仍14未完成。

旧Critic在10:19:30、正式stop前因7200秒synth_timeout生成evt-6bc3cc89b7de→prd.plan.failed evt-b7cf6832b3b1，非对7742570b版的新语义否决。恢复后自动stage replan 2/2产生evt-389aef82b01a，Planner已实际运行（pane %807）。未覆盖历史失败或增加replan cap；最新7742570b冻结19文件逐字节复制到Planner允许忽略缓存，SHA索引quota-restored-planner-input-cache.json。指导其完整消费本attempt必读后复用已修订F1/F2正文、保留36done与两个在途任务、继续独立Critic及审批，避免退回旧版全量重写。三角色断点指导与新身份记录在quota-restored-guidance.json。额外Sources浏览器失败仍保留、不改判。

Dashboard HTTP200。R5继续active；本次没有新的产品done，也没有声明计划已批准。记录待后续评估：全局暂停期间synth墙钟超时仍会产生计划失败及返修；本轮已走既有有界返修路径，不据此临时取消超时或伪造Critic通过。
