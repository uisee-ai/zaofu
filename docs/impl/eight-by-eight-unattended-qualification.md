# 8×8 无监工真实验证记录

## 2026-11-20：启动前环境缺口

基于 dev `e178f43c` 新工作区启动隔离 PRD Run `workflow-055abbd5196f67c6`。
OA 使用 gpt-6-astra，其他配置角色使用 gpt-6-luna。8 Impl + 8 Task Verify 为
目标容量，尚未形成批准的 16 项任务，也未进入 Plan 批准后的无人干预窗口。

两名扫描 Reader 执行命令遇 `bwrap: loopback: Failed RTM_NEWADDR: Operation not permitted`。
Runtime 观察到了无结果终止并按有界恢复处理，最终生成失败、Supervisor attention 和
Run Manager 诊断请求；RM 自身也受同一环境问题阻塞，无法读取材料或提交 recommendation。
不能把这种有检测但无修复的状态写成无人恢复成功。工作区、账本、原失败均保留。

手动模型预检只返回文本，未证明工具可启动；这是本轮操作者漏检。
已有 `zf doctor provider` 的真实沙箱探针可以复现故障，说明探针本身并未失效。
另一处缺口是 `zf start` 未调用探针，导致漏跑手动 doctor 时真实模型仍会被派发。
本次将现有探针接入 start，在启动 transport 前拒绝受限 Codex 的不可用沙箱。
失败通过 EventWriter 记录 `env.preflight.failed`，不关闭沙箱或改变模型。
详见 [Provider 预检手册](../manual/16-real-codex-provider-preflight.md)。

调用方回归：`tests/test_cli_start_provider_sandbox.py`，共同探针回归：
`tests/test_provider_sandbox.py`。探针只验证命令启动机制，不代表完整工作区能力或业务验收。
宿主仍缺发行版 bubblewrap/AppArmor profile，当前用户的非交互 sudo 要求密码；
需管理员执行现有 `scripts/setup-codex-sandbox.sh --apply` 后，以普通服务用户重验。
系统安全限制没有放宽。

本轮停止是操作者介入，发生于 Plan 批准前；若将本次启动也纳入端到端无人干预资格，
该项不能通过。计划后窗口尚未开始，业务、8+8 容量与最终交付均未验证。

## 2026-11-20：自动重试后 Reader 恢复被错误拒绝

保存的真实账本显示：扫描 Reader 的失败 `run_id` 为 retry-1 派发，但 Operation
的 durable `active_attempt_id` 保持首次 call 身份。二者分层本身正确；旧
`reader_rebuild_error` 将两者直接比较，导致即使宿主修复，child-rebuild 仍拒绝。

准入改为沿最新 kernel 派发核对失败的 transport ID，再核对嵌套 call 的 Operation、
请求摘要、workflow Run 和 durable attempt。外层 TaskAttempt 不冒充 call attempt；
不重写旧 Operation、放宽 Run 终态或跳过侧车校验。尚未派发的环境等待仍走原分支。

`tests/test_reader_rebuild_after_transport_retry.py` 从真实 Orchestrator/mock transport
的两次 Provider turn closed 重现失败，经 Run Manager recommendation → controlled
child-rebuild → Kernel 派发验证恢复；同时覆盖进程重建、幂等和错误身份拒绝。
真实 Provider 的恢复与后续 8×8 仍等待宿主修复，mock 通过不替代该层结论。

## 2026-11-20：宿主修复与旧升级记录关闭

获得用户授权后，执行现有 `scripts/setup-codex-sandbox.sh --apply` 安装发行版
bubblewrap 与 AppArmor profile，普通用户 doctor 实际沙箱命令成功。userns 限制
保持开启；没有关闭 Provider 沙箱。隔离 systemd runtime 重新启动，RM 原生会话
已能执行工具，但旧失败对应的人工升级仍阻止后续恢复。

已有 ControlledActionService 支持以精确 escalation_event_id、approval_ref 和
reason 关闭无合同字段的旧升级。Web 入口却提前统一拒绝，导致安全兼容路径不可达。
本轮仅把 legacy dismiss 接回已有实现，保持 token、当前性、幂等和绑定检查；
无合同的批准恢复仍拒绝。实际 Web 入口到 RM 消费由
`tests/test_web_legacy_escalation_dismiss.py` 覆盖。关闭提示不等于恢复任务，
后续必须观察新的合法 Reader Operation、派发和产物。

宿主修复、重启与人工关闭发生在 Plan 批准前。16 项业务验收和 8+8 并发尚未开始，
本段操作不能计入无人干预成功证据。

## 2026-11-20：部分 Reader 恢复后再次被人工门阻塞

15:21 UTC RM 精确 child-rebuild 已经通过实际准入，新 product-scan Operation 完成
真实 Luna 执行，15:25产物获准。另一 tech-scan 仍为旧失败，重新汇总后 reactor
对 infrastructure_failure 无条件升级 human，挡住已有 RM 诊断责任。现在只有
启用 resident RM 且该事件注册了 RM pending_action 时，保留该路由；未具备这两个
条件仍升级。此改变不批准任何重试，不改变审批、语义重规划或失败事实。

同时修正失败汇总把所有历史 fanout.child.failed 重新注入的缺陷：只补充当前
failed child、当前派发和当前事件的正文。成功或新派发不能被旧报告污染；历史
仍保留账本供诊断。针对两个真实问题新增调用方和多 child 重试回归：
`tests/test_reader_infrastructure_recovery_routing.py`、
`tests/test_fanout_current_failure_findings.py`。真实后续阶段须单独记录，不能从这些
测试推断16项完成或8+8并发通过。

## 2026-11-20：同问题的新失败被旧诊断去重吞掉

解除重复人工门后，只读复验发现最新 `prd.scan.failed` actionable、尚无后续成功，
但其 checkpoint 只由问题 fingerprint 计算；旧诊断的已派发回执导致新失败永远 seen。
再加上从旧到新的 fingerprint 去重，部分恢复后的新输入没有获得诊断机会。

对带 fanout_id 且明确 infra 的发生，将 checkpoint 绑定实际事件 ID，保留稳定问题
fingerprint，并选择同问题的最新失败。相同事件重放仍抑制，历史不改写，仍必须经过
RM裁决及受控准入才能重试。`tests/test_reader_recovery_diagnosis_occurrence.py`
经实际RM投影验证这一选择及幂等；普通语义失败、权限和预算策略不在此修复范围。

## 2026-11-20：单 child 成功不能消费较新的扫描失败

完整投影复验进一步发现：新 checkpoint 已产生，但旧恢复 case 因第一个 child
成功而 resolved，仍把新诊断过滤掉。真实剩余 tech-scan 由重启后的 RM 会话主动
恢复，并于15:59完成；这不证明该自动诊断队列正确，也不能算零干预通过。

现在只有前一 child-rebuild 的内核 effect 成功、同 Run/同 fanout、较新的内核
infra 失败输入，才能重新获得诊断准入。case identity 与历史保持；活动 effect、
人工等待、旧事件重放、异作用域及直接变更动作均不获豁免。队列选择与执行前
使用相同校验。新输入的业务含义与恢复方案仍由 RM 判断，内核不代替裁决。
`tests/test_reader_resolved_case_diagnosis.py` 覆盖完整 RM 投影与准入反例。

## 2026-11-20：作者 ref/sha256 工件在提交预检中不可读

真实Planner已写出11项有摘要的工件，但result validate仍只按state_dir sidecar读取，
在Kernel正常搬移发生前拒绝。既有ports_materialized标记现在传入正文读取：作者
预检允许当前project_root内的文件；已经搬移的包继续严格要求sidecar。已有运行态
绑定不能由作者同名文件替代；路径逃逸、符号链接逃逸、摘要错误及缺失继续拒绝。
预检不搬移文件、不写事件；实际交付仍走原受控提交与工件搬移。
`tests/test_author_plan_port_preflight.py`覆盖实际Service validate/submit→Kernel搬移→
下游全文/摘要保留及边界反例。该修复不放过Planner另有的schema错误或语义缺口。

## 2026-11-20：已完成Reader触发成本停更误报

15:44 UTC的cost.usage.blackout引用15:21的product-scan派发，但该child在15:25
已经完成。检测器只排除Run终态，错误保留了child活动表面。现在按内核记录的
workflow_run_id/fanout_id/child_id/run_id完整身份排除已完成或失败的Reader，
继续查找尚未结束的派发。其他child、旧重试、异Run和worker自报不能关闭当前派发。
不调整预算、阈值或冷却；未知身份保留旧保守行为。
`tests/test_cost_blackout_reader_terminal.py`通过真实tick入口验证这些边界；
原告警时刻的账本只读重放不再触发告警，不改写原事件或伪造关闭结果。

## 2026-11-20：空值已完整交付却无法满足必读策略

Planner返工的`$.backlog_ref`是冻结空字符串，完整受控空页已进入账本并被
Provider观察，但自动生成策略仍要求至少1字节，导致反复补读无法完成准入。
新派发只在精确source/path的已冻结无损选择证明内容为空时，自动声明最小0字节。
该字段仍是必读：缺收据、错身份、错摘要和截断均不能满足。未知或非空选择仍为1；
显式最小字节约束和旧attempt不可变策略保持原值，修复后需受控新尝试，不能改旧账本。
`tests/test_empty_required_read_dispatch.py`经过真实`prepare_call_operation`、
重规划工件分区、策略冻结和页读取入口验证，保留默认值及身份反例。

修复`a8ea58a5`通过63聚焦、9269影响闭包（7 skipped）、Web、53哨兵、10流程测试。受控stage-retrigger保留原始来源和修订草稿，内核取消旧attempt并建立新attempt；71/71读取覆盖后，2026-11-20 17:16:57真实提交获接纳（`evt-22a89feb7a39`），随后独立Critic启动。旧policy/账本保留，未代写Worker结果。这是准备阶段修复闭环，业务验收与Plan后无监工窗口仍未开始。

## 2026-11-20：默认Git守卫阻塞预授权宿主发布

首个Plan后只读窗口于17:25:47开始。16个独立Provider turn绑定到8 Impl与8 Verify，原生日志重叠227.597129秒；8审计全部完成。8个TDD Worker保留源码/测试和RED/GREEN证据，但sandbox不允许写共用Git元数据，按设计交给宿主发布；随后宿主边界拒绝了`zf init`自己安装的标准pre-commit。安装体与`PRE_COMMIT_HOOK`逐字相同。

本轮并发容量通过，完整无人交付未通过；窗口内0人工恢复不等于交付成功。全部业务operation结算后受控停止，保留8个实现工作区和原事件，修复在窗口外进行。

修复仅认可可信运行时代码内置、默认位置、无符号链接、字节完全相同的pre-commit；未知hook、修改体与core.hooksPath仍拒绝。宿主manifest冻结hook摘要与模式，执行前复验；Git仍实际执行hook，不删除或绕过原守卫，不替Worker宣告实现通过。

验证：92项聚焦、9390项影响闭包（7 skipped）、Web typecheck/unit、53哨兵和10流程测试通过。真实修复`85fc8614`已加载：8/8任务自动发布、8/8原生后续retry，保留原工作区/session，首项已进入独立Verify。产品最终交付继续验证。普通retry在缺宿主发布收据时保守拒绝（CLI统一提示budget exhausted）；恢复需有效发布收据或当前失败对应的显式单代授权，不能据worker失败标签免费无限重试。

## 2026-11-20：只读Python筛选误判为写入

接续期间Run Manager只读查询events.jsonl，Python比较运算符`>=`被shell词法分析当作重定向，PreToolUse拒绝了没有写目标的查询。Agent自行改用其他只读查询，业务未停。修复在独立worktree验证，不热更新当前验收窗口。

守卫只对一个带引用heredoc、可证明无文件写入的有限Python AST子集免除shell重定向误判：允许JSON/Path读取、局部赋值、循环筛选与stdout输出。未知程序、动态调用、危险import、可调用名称重绑定、属性赋值、写模式open、shell尾随命令均保留原拒绝判断；不执行待检查代码，不扩大文件访问范围。真实Hook入口覆盖dev和Run Manager。

## 2026-11-20：候选恢复中的诊断与宿主边界

16/16业务任务通过独立Verify并整合到冻结候选`09094c2d`。首次候选8路派发跨过60秒watchdog，宿主自动重启后补派剩余2路，总计8个唯一child；源码树/入口代码摘要和候选不变。此项证明自动续接，不证明最终验收。

候选Worker报告浏览器执行环境未就绪后，Runtime按预授权自动执行Docker命令。Resident案卷只列reflect/source_repair等能力，未显示宿主验证声明和执行进度，Agent在已有队列推进时误建议人工修环境。现在实际诊断sidecar增加同Run宿主策略声明、可信执行事件和绑定收据；坏收据保留unavailable，异Run/非可信自报不混入。方法要求核对合同命令、最新执行owner和唤醒条件；不把部分非浏览器收据理解为没有恢复队列，不替Agent作决定或解除human gate。

宿主probe原先只在遍历到owner后才抑制兄弟执行，较早注册、稍后失败的Reader会重复抢占。修复先检查整个精确Run/fanout/target/contract cohort，优先保留最早真实宿主effect owner；已有收据和结果未知的effect均保留责任。异scope不合并，未知结果继续要求检查，不凭新owner重试。

浏览器采证再次被60秒watchdog中断，宿主命令允许180秒。配置现在拒绝启用watchdog且其上限小于等于同步宿主命令timeout的组合；操作者还须为其余tick开销留余量。本实验后续配置300秒，不增加后台心跳、不关闭watchdog。冻结脚本生成约24MB移动端trace，原20MiB逐文件限制不足。`runtime.host_verification.max_artifact_bytes`默认20MiB、可配置至64MiB；`max_total_evidence_bytes`默认64MiB、最高128MiB且不小于逐文件值。保留常规文件/链接限制。旧执行策略摘要只在执行字段相同且实际旧采证字节满足当前上下限时兼容，不假设旧实现有总量限制。

原观察窗口已关闭，暂停记录和未知宿主effect保留。新真实窗口需单独记录接续、Docker验收、Judge、Goal和本地交付；不能追认原窗口无人通过。

验证：实际入口回归6红转绿；owner乱序回归1红/9绿转全绿。合并聚焦121项通过，补充owner未知结果/乱序与限制50项通过（有重叠）。影响闭包首跑13,210 passed、17 skipped，因测试环境缺少controller依赖有13收集错误和1失败；使用依赖齐全的实验venv对14个受影响文件补跑182 passed。Web typecheck/unit、53哨兵、10流程、2项真实Docker执行器检查通过。重复后端验证因错误tier名被操作者停止，已用正确deterministic tier完成剩余检查，未停止运行态进程。当前代码尚待窗口外部署，真实接续结论单独记录。

## 2026-11-20：恢复通知关闭与重新审核说明

修复`5a0a9682`已在窗口外部署，18/19条冻结宿主命令有有效收据；浏览器旧操作保持未知，未自动重跑。Web接受关闭误报通知后，Run Manager消费端却将合法`dismissed`记为拒绝，并被通用action字段覆盖ack来源和reason。现仅对匹配已有升级的dismiss记录非变更的已应用结果，保留准确来源和说明；不执行恢复、不重新调度、不产生业务通过，未知token仍拒绝，重复消费幂等。

同一候选的显式`stage-retrigger`原先忽略操作者恢复说明。现沿用已有feedback校验与冻结repair-context路径，在任何修复副作用之前拒绝非法说明，保留候选、任务与原工件绑定；未提供说明时不改变既有行为。恢复说明只是有来源的待审核输入，不替代独立业务裁决或豁免验收。首次候选中的数值范围争议与缺失浏览器证据须完整传递，不能将mixed失败伪装成纯基础设施失败。

同一冻结候选的 `stage-retrigger` 通过 `candidate_snapshot_event_id` 指向当前内核冻结事件。新的 Reader 复用满足原绑定的命令回执和媒体，独立判断验收项。候选已被后续冻结替代时，控制动作返回冲突。

验证：3项红回归已复现；修复后22项聚焦通过，当前增量影响闭包6112 passed、9 skipped，53哨兵和10流程mock通过。采用上一已验证提交5a0a9682作为增量执行基线，同时保留相对dev的全分支验证计划；真实接续另行记录。

## 2026-11-20：首轮启动期限独立于watchdog

19:14:43受控启动后，19:16:14被systemd默认90秒启动期限终止；已配置的300秒watchdog未覆盖READY之前的首轮批量Reader准备。宿主监督在19:16:20自动重启并继续派发，当前观察没有人工恢复。用户服务生成器现为notify模式显式设置`TimeoutStartSec=max(90, host_watchdog_seconds)`，保留有限启动上限和完整tick之后才通知READY的约束。该配置不是任意规模启动的时延保证；更重的启动仍需相应部署预算或有界首轮执行。此修复在独立源码分支验证，当前真实窗口继续使用8255b300，不热更新。

验证：3项启动期限回归先红后绿；17聚焦、25影响闭包（含design200宿主监督mock）、53哨兵通过。真实旧版本自动重启后8个唯一child均已派发，无重复；当前未部署本次启动期限修复，不能将旧版本的自动恢复当作新配置验证。

## 2026-11-20：旧失败恢复抢占已接纳的新审核

同一窗口中，RM仍将首轮候选的`test.failed`投影为`needs_stage_replan`，即使受控重触发已经被Kernel接纳并派发8路审核。19:18:08自动重规划产生另一trigger，19:18:19起Kernel取消8个在途operation，后补的恢复说明也没有随旧因果输入传递。当前窗口因此失败，并在修复前关闭。

修复在stage-replan投影和执行共用入口识别显式`redrive_of/rework_of`来源链：必须能找到原失败的可信fanout、同Run/同stage/任务代际的继任trigger及其更晚的Kernel fanout接纳事实。未接纳、自报、异来源、异Run/stage和循环/乱序引用不屏蔽旧失败。旧失败与发现继续保留，但其恢复已由新代际负责；这不表示业务通过，继任者自己的失败仍需独立恢复。

验证：1红7绿的实际ControlledActionService→恢复checkpoint回归转绿，54聚焦、4161影响闭包（7 skipped）、53哨兵与10流程通过。真实事件前缀21149条：旧代码planned=true，修复后planned=false/owned_by_admitted_reader_retrigger。未写运行态或改历史结果。部署前精确kernel provider_stop_confirmed+cancelled operation以及宿主进程/tmux不存在共同证明8个Reader已停止；保留未收到stop hook的旧记录，不伪造Provider收尾。

## 2026-11-20：已确认取消停止仍阻塞新派发

第二次接续的8路审核均在`provider_turn_active`等待。取消路径已确认transport退出并写入`provider_stop_confirmed`，但活动投影没有消费该停止路径，新的launch又被旧turn阻挡。现按可信kernel取消、相同role/dispatch/operation/request/attempt和取消事件配对`cancelling`与确认`idle`，只清除开始停止时捕获的旧turn集合。期间新turn继续活动，未确认、身份错误、缺开始或非kernel证据均不清除。实现复用现有正式回执，不补造Provider hook或业务完成事件。

验证：使用与实际Orchestrator一致的kernel EventWriter重新对基线复现失败；69聚焦、3267影响闭包、53哨兵和10流程通过。真实账本只读重放由8个活动角色变为0；未知/不匹配回执和停止期间的新turn反例保持。未补写hook或修改历史状态。

## 2026-11-20 19:52 UTC：修复后的真实接续证据

4d1564df部署后从已接纳c6870392 pending fanout继续，8个真实Reader均被派发，没有新增trigger；当前窗口只读、无人工恢复。启动期限300秒已部署，首轮跨90秒仍以同一PID完成并进入active。旧失败不再抢占，已确认停止的旧Provider不再占用派发。此观察不证明任意启动负载都受有限期限保护。

真实RM案卷rmd-2d226c8e1e519f24包含启用的19条宿主命令、授权和同Run执行事实，Agent核对后提出有owner与唤醒条件的等待，runtime已消费。新cohort的19条冻结Docker命令始终由同一lane6 owner完成。浏览器正式收据evt-db22dcbf5981记录exit0、source_stable=true；固定候选09094c2d及镜像绑定正确，桌面/移动16功能、非法输入恢复、键盘和布局断言成功。PNG/trace/WebM/environment均通过摘要核验，当前控制器未重启。旧未知浏览器effect不被覆盖或追认。

此时只可判定16项Task、8×8容量及19条最终命令通过；Candidate独立总判定、Judge、Goal、本地交付仍待执行。原始Plan后完整无人资格未通过。局部修复与本轮后续接续不可改变首窗口失败结论。


## 2026-11-20 候选混合失败交接修复

真实Run `workflow-055abbd5196f67c6` 在16项业务完成、8+8真实重叠227.597129秒后，
最终冻结候选的19条宿主命令（含原始浏览器脚本）全部执行成功。最终Reader汇总仍为
mixed：6路环境弃权、1路依赖阻塞、1路语义拒绝。宿主成功不代替这些独立结果，
数值安全范围争议保持未裁决。20:12 UTC关闭本轮只读观察并停止专属控制器进行修复。

已复现的交接缺口及边界：

- 宿主队列完成后只对infra失败请求原地Reader恢复；mixed/quality/unknown保留给语义责任方，不能改标签绕过恢复条件。
- Candidate上限触发的RM请求以当前已准入的整个失败汇总创建OA checkpoint，不依赖工作流锚点的不存在的TaskAttempt。所有失败Reader结果、当前候选/合同/计划、作用域和新宿主事实均进入证据包；主Reader仅为身份锚点。
- 受控重启事件保持operator来源，Kernel同Run fanout准入且冻结身份不变才承认其新候选事件身份；伪造redrive、缺准入、异Run、目标变化不能获得恢复权。
- OA提交时再次核验全部成员引用与当前候选，仍只允许请求Plan修订，不授予writer、通过或门禁豁免。未知AC作用域留给Planner判断。
- RM后置验证按配置实际选择semantic或legacy入口事件；入口请求成功不等于Agent已完成分诊。
- Verify方法区分Impl缓存复用与同一候选恢复链上的独立宿主执行事实，保留真实execution_origin与执行身份。新宿主事实与历史缺证结果并列，不能自动变成产品通过。

回归覆盖真实RM→OA入口、多Reader证据保留、摘要篡改/失效操作/过期候选/缺成员、
操作员重启准入、混合失败不重复请求infra恢复，以及OA接收同组新宿主事实。
实际恢复后的OA决定、最终Candidate/Judge/Goal/交付须另行验证。原始无人监工窗口
失败不因后续修复被改写。任务记录：
[tasks/done/2026-11-20-2013-candidate-semantic-handoff.md](../../tasks/done/2026-11-20-2013-candidate-semantic-handoff.md)。

本补修相对aa4a9611的自动影响闭包6072 passed/8 skipped；sentinel53 passed，flow mock10 passed；聚焦108 passed与闭包有重叠，不合并计数。未运行全pytest或coverage。真实账本只读回放确认8路结果与19条宿主收据绑定同一冻结候选，实际OA接续尚待部署验证。

真实接续已验证：69b2a6e1载入后，OA输入包含8路结果、19条回执及浏览器媒体索引；其return_to_plan决定于20:38:45由Kernel接纳应用，自动启动限定原16任务范围的Planner恢复。保留原失败和冻结候选，未直接重开Task或授予产品通过。最终Candidate/Judge/Goal/交付仍待后续原生结果，初始无人监工失败不变。


## 2026-11-24 扫描工作目录的沙箱可见性

批准前的 Run `workflow-c87649819cbe0bb7` 派发了两个扫描 Agent。宿主能够读取简报，
实际 Codex 工作目录却看不到相邻的简报和 attempt manifest。原启动探针只运行
`/usr/bin/true`，因此放行了这个目录布局。相同 Provider 沙箱的只读探针复现了该差异。

受限 Codex 角色启动时，`zf start` 在配置的 state_dir 中创建临时工作目录，
从该目录经同一 workspace profile 读取相邻的随机标记，并核对内容。失败沿已有
`env.preflight.failed` 入口在模型派发前返回。探针文件由宿主清理。实际 Reader
仍须通过正式接口提交与本 attempt 绑定的收据；Plan 批准后另计无人干预资格。


## 角色登记读取中断后的 watchdog

B 轮 05:04 UTC，watcher 已选出在用角色，检查其生命周期时再读 `role_sessions.yaml` 遇到文件缺失。宿主监督重启了控制面，同一 Run 接着集成任务。此异常只证明打开文件时路径缺失，日志未标明移除者。若再次发生，日志记录角色和路径，该角色本次存活检查延至下一次轮询，保留已有会话绑定与死亡计数；其余角色继续检查。


## Candidate 预检缺少宿主回执引用

实跑中，Reader 拿到同一冻结目标的 20 条宿主回执，首次通过草稿只引用了 3 条，预检准确指出另 17 条命令缺少证据，却只指向 `probe_receipts`。Reader 随后自行在 `host_execution_receipt_ids` 补齐 20 条，独立评价 16 项验收条件，原 attempt 得到通过。对已绑定宿主证据索引的 Candidate，预检现在同时指出这两个证据入口。Reader 按 `command_id` 查找、审阅并显式引用对应的宿主回执，只把自己实际执行的检查登记为 probe。缺失、越权和过期的证据继续按原门禁退回。

候选提交草稿还会按当前 Reader 已绑定的宿主观察区分证据来源。对已有可用宿主观察的命令，模板省去预填的本地成功 probe 行，`host_execution_receipt_ids` 初始为空，由 Reader 查验后填写。缺少可用宿主观察的命令仍有本地 probe 模板；所有命令 ID 及验收义务继续来自冻结合同。当前草稿、已提交判词及旧 attempt 的证据不会被模板更新重写。
