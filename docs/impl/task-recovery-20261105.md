# Task Pipeline 断点修复（2026-11-05）

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
