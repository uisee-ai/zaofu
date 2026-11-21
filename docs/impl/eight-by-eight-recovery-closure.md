# 8×8 执行与恢复链路修复

2026-11-21 环境污染补充：源码修复有两个执行边界——Worker 自行测试，以及 RM 的
独立验收。两者均经 `repair_process_environment.isolated_repair_environment` 剥离
父控制面的 `ZF_*`、Git/tmux 和 Python/pytest 定位，显式注入可信解释器、本地 `src`
与一次性 workspace。Provider 的现有 sandbox 和凭据配置保持。真实子进程回归证明
项目事件文件不被污染；后代进程清理使用下述独立边界，不能由环境隔离证明替代。

NEW 验证准备期曾被测试 `zf init --force` 重置运行状态。17:09 UTC 恢复可信检查点
（2818 条，截至16:22:19），保留原 session ID、六个已终结 TaskAttempt、引用 sidecar，
隔离污染投影和后续进程结果，绑定安装后的消费游标并保持维护暂停。未从索引或其他
Run 拼造账本，检查点后的原始差量完整性保持 unknown；不计入无监工通过。

2026-11-21 完成任务保护：Plan派发先用拟议Task视图执行admission，capacity在落盘前检查，
已拒绝派发不得先重开done任务。准确晚到批准只有package正文与显式端口匹配、Candidate
仍为原提交、所有相关Task与原生/复用完成证据当前有效时才抑制重复派发；不授予Goal或Judge通过。
integration receipt绑定捕获时的合同语义版本与authority token，Candidate写入前重查token；
terminal核对两者、检查integration后的合同/派发事件，再以TaskStore CAS绑定authority、status和active_dispatch_id完成。
`terminal_tasks`归类为Task状态准入冲突，kernel-origin的`fanout.cancelled`自身即交RM诊断，后续`task.fanout.rejected`只作同incident审计，禁止继承
旧schema_gap去重规划。两次事件追加间崩溃可补齐诊断，不因cancel已存在永久静默。
这组改动防止复发；14项既有受影响完成记录尚待独立、可审计的受控补偿。

NEW于17:15从可信检查点恢复后的原Plan阶段继续，真实Planner冻结输入已经含TODO与
SOURCE-PROMPT完整必读正文。恢复步骤包含参数纠正和transport bootstrap，均为人工介入。
本次准备Run及旧失败窗口不改判无监工通过，新资格窗口仍未开始。

终态历史补偿使用受控`stage-retrigger`的独立`restore_completed_tasks`分支，提前返回，
不得与普通重发选项混用。仅补偿已拒绝Plan重放造成的done合同替换；精确Owner意图
绑定不可变预览，核对暂停Run、当前Candidate、归档与原完成证明。存储层在原归档桶
一次追加整批新authority，完成时间仍为原时间，历史记录不改写、索引不搬迁、无backlog
中间态。旧格式execution binding经既有解析器规范化后计算新token，原历史body仍保留。消费者需携带completion_restore_ref，旧Verify identity不被伪造成新的验证；
新authority或证据变化继续拒绝。无新业务trigger、task.done、Judge或Goal通过。

本说明对应 [实施任务](../../tasks/active/2026-11-18-0001-eight-by-eight-recovery-closure.md)。基于 dev dda38a25 的隔离实验发现；原实验仅证明 Impl 峰值 8、Verify 峰值 5、总同时峰值 11，1/16 完成，未证明真实同时 8+8 或最终产品交付。

## 修复边界

1. 新生成的人工 gate 保留显式 task/run/side/release 范围；未声明而绑定具体任务的 gate 默认 task，未知范围及缺任务身份的 task gate 保守按 run。既有 gate 不追溯改写。人工推荐消费前检查当前 run 进度与绑定任务 operation/attempt；过期建议留 superseded 收据，不新增 gate。Planner/Reader 在 Task Pipeline 之前按自身 role/operation 身份链校验，不能被当成无有效任务而丢弃；并行其他 reader 不替代其所有权。既有 run gate 继续通过授权入口处理。
2. OA 在队列重放和实际投递前，以相同不可变 execution_context 核对 operation bindings；同 Plan 的 G1→G2 也使排队请求过期。运行中请求仍需已有的绑定结果/关闭证据，不因版本变化提前释放。结果准入也使用此检查，包含 block/escalate 路由。
3. OA checkpoint 与 worker guidance 共用 FIFO；求助超过自身期限一半获得排队优先级。已投递求助、未关闭 provider turn 均占用同一个 OA，禁止双投递。期限到达仍明确进入 task 级等待，不能据此假装答复。
4. PRD 配置 v5.1.0 显式声明 `planInputPolicy: task-scoped-v1`。编译后的 required ports 包含 execution_spec、task_execution_input 和完整 AC/test/real-E2E 矩阵；每个 Task 必须由 Planner 声明 plan_input_selection。preflight 和正式 Package admission 均校验。Impl/Task Verify 读共享与本任务分区；Candidate Verify 保留全局读取。语义覆盖由 Planner/Critic 审核，不在 Kernel 以删字段或字数截断实现。未选择该策略的既有 profile 保持 full；旧批准不可复用于改变后的输入合同。
5. `host_publication_required` 表示受限宿主发布需求，不是产品失败。Worker 保留精确路径、提交基线及失败命令证据；RM 的既有发布器负责路径授权、静止工作区、预算、Git 效果和收据，OA 不进行语义返工诊断。未开启发布策略时仍进入诊断。发布结果不替代 Impl 自检、交接阅读及独立 Verify；干净工作区却请求无收据的发布，明确升级为 task gate。
6. operator retry 与调度器均按实际 admitted 语义失败计数；包含 Verify 的 product_rejection，不按 operation generation 扣额度。reader/input 等基础设施失败独立计数，使用同一配置上限作为独立有限额度；宿主发布使用配置的独立有限预算和精确完成收据。已有单代额外批准仍绑定下一代，不产生无限重试。
7. AR 默认 runner 每次有界等待后观察其自有子项目的持久状态；run 级 owner gate 及时产出 child_owner_wait 部分诊断、保留 refs、正常停止子运行。task/side/release gate 不被误判为整个子流程停滞。max_minutes 是工作期限，不再乘二；controller 终止与 runtime cleanup 分别有明确短超时。父 RM 保留 typed failure_class/reason，不将未完成统一误报为环境故障。部分诊断不确认 source defect、不触发未经证实的自动修复。

## 分层验收

- 定向回归：实际 producer → consumer，含 gate 范围、同 Plan 换代、忙队列求助、发布收据/额度、AR 等待和停止路径。
- PRD 输入集成：加载真实配置，经 Package 准入、Task snapshot 和 handoff manifest；增加 64 个无关任务，T1 必读正文保持相同，Candidate 仍完整读取。
- 容量：真实调度器、独立工作区及持有进程的 deterministic mock E2E 验证 8 Impl + 8 Verify 重叠、重放与背压。此层不是 provider 吞吐证明。
- 真实 PRD：隔离项目、私有 tmux、8/8 pools、人工 Plan 审查（用户已授权本 session 代审），以全部业务 Task、独立 Verify、Candidate/Judge 和 Docker 浏览器断言为完成条件。记录容量观察但不因未捕获 8+8 瞬时窗口提前结束业务闭环。失败时保留断点，修复必须重测并记录介入。

验证命令：`python scripts/dev-verify.py plan --base dev`、`python scripts/dev-verify.py run --base dev`；精确结果、真实验证过程见本次 reports 目录与任务记录。尚未执行的 gate 不能标通过。

## 明确保留的限制

本次没有赋予 worker 广泛 Git 写权限，也没有把人工 Plan gate 自动批准固化到运行时。实际 owner 决策、外部额度/凭据不可用仍应等待。新 Task 必须读取自己的输入和交接，不能继承上一次 agent 的阅读证明。队列优先级不会中断正在运行的模型调用；长期占用仍交由既有 turn watchdog/有界恢复处理。

## 本轮验证记录（2026-11-18）

- 初始 RED 已复现显式 gate 范围被覆盖、同 Plan 旧 checkpoint 不退休等问题。
- PRD profile → Package → canonical Task → read manifest 集成通过；64 个无关任务增长不增加 T1 选定正文读取，Candidate 保留全局矩阵。
- 容量/发布安全/基线快照专项：84 passed，含独立进程实际 8+8 重叠；仅证明 mock 调度容量。
- Reader currentness 补充回归：238 passed（RM 消费入口、恢复步骤、失败路由、AR 诊断消费者）；有效 Reader 求助保留，旧 operation 求助拒绝。
- 真实宿主启动、mock provider：1 passed。覆盖配置批准、冻结交付基线、真正运行时启动、派发和清理；不是模型完成业务的证据。
- 重新同步 v5.1 的初始化与配置版本断言。基线已有的 11 项失败已在干净 dev 重现并仅更新过期测试预期，未改生产预算和唤醒集合；原始日志保留。
- 16 个独立功能、8/8 pools 的新隔离项目已准备且未启动。用户随后明确授权最小真实模型测试，另建一个业务 Task、1/1 pools 的项目；真实模型已执行，但未完成业务闭环，详见下节。

精确命令、退出码与分层结果保存在 `reports/eight-by-eight-recovery-20261118/`；报告目录不进入 Git，任务仍 active，待真实验收结论后归档。

自动影响闭包：`PATH="$PWD/.venv/bin:$PATH" python scripts/dev-verify.py run --base dev` → 8088 passed / 16 skipped；premerge sentinel 53 passed；flow smoke 10 passed。另行执行的 8+8 容量/安全专项 84 passed、Reader/RM 专项 238 passed、宿主 mock 启动 1 passed；这些集合存在重叠，不合并宣称唯一用例总数。未执行完整 pytest / coverage。

## 最小真实验收结果

2026-11-18 使用实现提交 `de770b5c`，运行 `workflow-5bb0af811aaa5397`（隔离目录 `/tmp/zf-minimal-real-20261118T013230Z`）。Terra High 的两个 Scan 完成，Astra High 的首轮 Planner/Critic 均完成结果提交；Critic 的语义结果为 needs_rework，不能视为 Plan 已批准。测试 PRD 未明确 Enter 焦点，本 session 经受控 Owner 决策确认冻结脚本的按钮焦点语义，系统自动派发 Planner 返工。

返工 Planner 的必读选择量为 182,040 字节，首轮为 17,381 字节。上一轮 prepared report 与 control result 中完全相同的 `plan_ports` 同时被要求读取；原生 context compaction 实际发生。这里暴露的是 Plan 返工输入重复，与已实现的 Impl/Task Verify task-scoped 输入属于不同链路。尚未派发 Impl，因此不能用本次运行证明 worker 已消费裁剪输入。

运行在 1800.281 秒由 Kernel 自动触发 wall-clock budget gate，取消 Planner 并记录 run.goal.blocked；业务完成 0/1，最终验证未执行。配置费用上限 $10，但 110 条 usage 未定价且 cost_fail_closed=false，不能宣称费用门禁通过或没有消耗。预算停止后，试验专属服务及 tmux 已清理、恢复证据保留。报告位于 `reports/minimal-real-20261118/README.md`。

下一轮需先消除最小输入歧义，并为局部 Plan 修订去除重复正文、保留来源摘要绑定和 Owner 确认；独立审查、审批和最终业务验收继续保留。此记录不将上述待修问题或真实 8+8 验收标为已完成。

Owner 随后明确纠正验证范围为原定 8×8，并要求取消 Planner 时间限制。已启动另一个 Run `workflow-8625b886a7912513`：16 个独立小任务、Impl/Verify 各 8、活跃流水线与背压上限 16。复用未启动的 8×8 配置，其 Run、execution profile、Planner stage timeout 原本均为 0，不再添加上一轮的 1800 秒覆盖；失联检测、审批与独立验收保留。补明冻结测试输入的 Enter 焦点，未修改验收脚本。当前仅确认真实 Scan/Plan 启动，重复返工输入缺口和实际 8+8 重叠尚未验证解决；启动证据见 `reports/eight-by-eight-real-resumed-20261118/README.md`。

## 真实续测发现的槽位复用耦合

`workflow-8625b886a7912513` 已完成正式 Plan 批准并进入真实 Impl/Task Verify。观察到同一物理 Impl 槽位切换任务时，`ensure_stage_workspace_idle` 同时要求新、旧工作区独占空闲；旧工作区已移交给合法 Verify 后，其正常读锁仍会阻塞新任务的 Impl。历史 mixed 8+8 mock 从 verify-only 起步，未覆盖“同一批 writer 槽位先实现、再移交 reader、再复用”的顺序，因此未发现这个限制。

修复仅在可证明移交时排除旧工作区探测：旧 writer 已退出且无活跃 operation，其 Impl 结果已准入 passed；运行中的 Verify 必须绑定同一 Run、Task、Task Map、operation/workspace generation、当前 session binding 及同一旧工作区，且对应 transport 仍活跃。新工作区始终执行独占探测；身份不明、未准入、过期绑定、换代或 reader 已退出时保留原有拒绝路径。不停止合法 Verify，不扩大 worker 权限。

回归通过实际 `_activate_task_stage_binding` 入口和真实文件锁复现 RED，再覆盖允许复用及上述拒绝条件。真实运行继续使用冻结版本；修复先在独立 worktree 验证，未经部署不能宣称本轮真实吞吐已因此改善。

补修最终影响闭包230 passed、premerge sentinel53 passed，生命周期专项61 passed（集合重叠）。全分支`--base dev`规划保留；相对已验证f90dbf80运行本次自动闭包，含调度/接力/锁/工作区/操作事件和8槽mock E2E。未运行全pytest或新增真实provider测试；现有真实Run继续保留原源码身份。

## 最终 Candidate 的环境失败闭环（真实续测）

### 已完成任务在 Plan 恢复后重复实施（2026-11-20）

已终结的 Candidate fanout 也可能遗留暂停的 Reader operation，不能把聚合失败等同于
所有环境占用已经释放。周期派发扫描在跳过终结 fanout 前，检查其活跃 operation；
只有同 Run 的 Kernel 准入证明确认过期，才沿既有取消/transport 所有权路径释放旧
持有者。保留原聚合结果，不补写 `fanout.cancelled` 覆盖失败，不重试旧 Reader，也不
重置其 attempt 上限。当前代际和其他 Run 的持有者仍受保护。
见[终结 Reader 清理](../../tasks/done/2026-11-21-0027-terminal-reader-owner-closeout.md)。

连续 Plan 修订还需追溯更早的原生 Task 验收。第二次恢复不能要求上一代重新产生
`task.done`：上一代可能只保存已批准的完成复用。预览以该代归档 Task 建立历史读视图，
验证完整复用引用链，再绑定最初的 Verify/集成事件和原始任务派发代际；新代际的
Candidate 继续校验当前合同与整条不可变链。破损引用、变更目标或原生证据缺失均拒绝。

若标准开发任务只在既有 `candidate_verify` 最终命令追加 `producer_paths`，预览完整
列出新增义务，受控动作还要求逐 Task 的 `candidate_evidence_review`，与精确 manifest
摘要、批准引用及复用理由一同持久化。此范围不覆盖只读审计、Task 本地验收、命令或
权限变化，也不允许删除既有证据。追加的产物仍必须由新 Candidate 阶段验证，旧 Task
完成证明不构成该项通过。见[连续恢复任务](../../tasks/done/2026-11-21-0010-repeated-plan-completion-reuse.md)。

真实实验的新 Plan 更新恢复基线与来源边界说明，原 16 项已完成，但完整 Task Map 的新代际令 runtime 全部重开。OA 的文字要求“保留完成证据”没有执行效力；pre_impl 为 shadow，所以不是 blocking gate 被绕过。

受控 `stage-retrigger` 增加 `reuse_completed_plan=true`：先 `dry_run=true` 生成完整新旧合同、原 native Verify/integration、原 Candidate 与当前 Plan 审批绑定的 manifest。Owner 审阅精确 `manifest_digest` 后，以 `reviewed_digest`、`reuse_rationale`、`approval_ref` 执行。只支持原完整 Task 集合、原冻结提交、验收/执行合同不变的 Plan 导航更新；拒绝 terminal Run、目标移动、过期审批、活动 Task、未停止或有改动的重新派发工作区。导航材料的语义适用性由审核人负责，运行时不从 ID 或文件存在推断。

运行时通过 TaskStore CAS 恢复原完成时间，保留旧 Task generation、native 回执和原 Candidate 基线；已停止的新 attempt 通过操作服务取消并结清，Task-stage 会话归档释放。`task.status_changed` 引用不可变复用证明，不制造新的 worker passed 或 task.done。Candidate 在新 Plan 下重新冻结，独立审核输入核验旧证明与当前合同的显式映射，合同/目标/证明再次变化时拒绝。Candidate、Judge、Goal 与交付结果均不继承。

真实续测还确认 verify_only 重派会重绑定 TaskRef，只有 Task 状态恢复不足以重新冻结。预览同时检查 TaskRef/index；只允许同 Run/代际对精确 Candidate 的只读重绑定，经原 TaskRefManager 的 Git CAS 恢复原已集成目标。未知变化或 writer ref 不回退。已经执行的完成复用可用 `repair_task_refs=true` 加新的精确审核摘要完成此步骤，不重复发出 driving event；原 native 集成回执继续按真实 TaskRef 校验，不放宽 receipt hydration。

操作顺序：完整 `zf stop` 关闭 transport 后，先 `zf start --no-watch --skip-workflow-inspect` 建立传输会话，再启动使用 `--control-plane-only` 的宿主监督 unit；只有 transport 保留的控制器重载才可直接启动该 unit。准备和受控恢复动作完成后再开启只读观察窗口。

入口只解决经过显式审阅的恢复；OA 自动提交等价绑定决定仍属后续能力，不能将此人工恢复记作零介入。真实8×8续测已由新Plan原生冻结原候选，8个最终Reader实际running，16项业务Task未再次派发；这仅证明本恢复入口生效，最终Candidate/Judge/Goal/交付仍独立待验。

后续真实Reader输入与结果准入仍报48条子任务旧身份冲突。共享 `candidate_currentness_issues` 现在重验同一不可变完成复用证明：当前Task完整合同/身份/完成态必须与审核一致，子项的复用ref和原Verify事件必须精确匹配，再以已审核原身份核对子项；不把旧任务伪装为新执行。未复用子项仍走原校验，目标/Plan/快照/current candidate门禁全部保留。`artifact index/recover` 和正式结果提交共用该入口，避免冻结接受而读取或提交拒绝。补修见 [Reader currentness闭环](../../tasks/done/2026-11-20-2205-plan-reuse-reader-currentness.md)。

受控checkpoint准入还必须被实际Reader派发消费。周期恢复优先核对kernel redrive、原workflow.resume.planned、不可变request及当前fanout/child，实际started使用同一个已准入决定；已授权停止恢复不计为新的传输故障，不重置普通故障预算。只有同operation原请求的这一次准入能触发，不凭restart文本或次数推断授权。已正式准入的failed/abstained Reader可经child-rebuild生成新operation，需核验原control-result摘要和身份；原settled事实保留，业务passed/rejected不适用。见 [Reader断点派发闭环](../../tasks/done/2026-11-20-2215-reader-resume-dispatch-closure.md)。相关任务见 [Plan 完成复用修复](../../tasks/done/2026-11-20-2120-plan-completion-reuse.md)。

16项任务完成后，最终8个reader中的7个在Docker socket受sandbox限制时结束回合，没有提交结构化失败；有界重试因此耗尽。调查进一步复现：即使按规范提交 `failed + abstained + verifier_execution_failure`，未完成产品必读材料仍导致准入失败，已有例外只接受 `reader_execution_failure`。这不应迫使agent把环境故障误报成输入故障，或先阅读全量产品材料再承认无法执行探针。

`task_input_failure` 仅为 verification-result 增加准确的 `verifier_execution_failure` 类别，仍要求failed/abstained、非空summary与evidence refs；身份、目标、读账本完整性、显式提供的产品报告检查继续执行。不产生通过或产品拒绝，不扩展Impl的环境失败例外。已消费部分仍封存，状态明确记录incomplete。恢复后的语义结果仍需完成必读和完整产品验收。

同步Verify skill：移除未执行的报告模板，未执行矩阵/探针留空，绑定当前operation并检查正式submit成功；聊天中告知等待不是交接完成。新增实际SemanticResultSubmitService回归覆盖Task/Candidate两条入口及8个负例。真实恢复须重新派发受控新operation，不代写worker结果或修改失败历史。

### 真实恢复的第二层缺口

同一候选重试时，Verify预校验明确提示移除未执行的产品报告，但briefing同时包含“预校验失败修正”和“任何错误停止”的冲突指令。第二轮lane3遵循停止指令，再次无结果终止。统一新派发和重试的提交ABI说明：预校验失败未消耗capability，可以按诊断原位修正；正式提交失败停止且保留诊断，绝不绕过身份/授权。Verifier输出卡提供失败结构规则，避免只改两个状态却保留通过模板。语义如何评估继续由skill负责。

恢复请求已准入的lane0在模型派发前启动空闲provider，工作区占用使重新pin等待；旧guard把从未交给provider的requested operation也当作活跃工作，无法释放空闲holder。新guard仅豁免匹配当前Run/stage/attempt的从未started或dispatched的reader请求；已有派发、其他Run/stage/attempt、writer、suspended/running、活跃turn或未知transport仍不释放。官方生命周期负责释放，不直接杀未知进程。真实临时恢复使用已审阅cancel_worker repair action释放lane0空闲provider，作为人工介入记录。

释放空闲holder与等待期限同一轮发生时，先给已确认释放的资源一次正常重新pin机会；仍被占用或释放失败继续按原期限退出，不重置计时。真实lane0先释放却立即超时的边界已回归复现。另一个真实故障是显式rerun的准备阶段ValueError被外层housekeeping吞掉，repair intent保持pending而无终态；转换为确定性的rerun失败，由RepairActionExecutor记录rejected及原因。该改动不自动重试、不新增权限，也不反写历史pending意图；旧历史pending项仍须单独对账。

### Candidate reader 重试的契约归属

修复动作落下明确拒绝原因后，发现重试把已有Candidate聚合快照按工作流锚点Task的局部契约重新校验；锚点只有血缘关系，没有TaskMap生成来源，因此首次派发能执行而重试失败。reader准备阶段按已验摘要的快照authority_scope识别Candidate，复用输入/结果准入的candidate_currentness检查；不凭profile名称跳过校验，不补写锚点Task契约，也不替换重试的冻结目标来迎合当前状态。

实际fresh-reader caller回归证明新operation/attempt/scratch与原调用不同，冻结contract/target/generation保持一致；错Run、任务、generation、目标、摘要、PlanPackage和已换代Candidate仍拒绝。普通Task与rescan继续沿各自权威路径。真实恢复和产品验收需另行证明。

真实重试已成功派发，但模型在大型通过模板中追加失败字段，留下重复JSON键和null产品报告，预校验正确拒绝。输出卡现提供同一verification-result.v1的精简执行失败示例，明确原位替换正文、使用实际探针证据、删除未执行字段；同时移除“保留预填字段”的笼统指令。方法同步到Verify skill。示例经真实预校验及SemanticResultSubmitService验证，不绕过任何准入，不替worker提交结果，也不以socket路径代替失败证据。

随后核实新reader结束回合后未被回收：受控rerun派发成功，但fanout投影仍为旧aggregate failed，恢复扫描忽略了它。投影现在仅在reader失败聚合收到绑定原operation/run的新调用派发时恢复pending，旧完成/取消聚合及无绑定派发不开放；保留旧聚合事件引用。补齐终态child落盘后、重新聚合前崩溃的恢复路径，不能因历史有aggregate.completed而忽略当前pending重试。实际RepairActionExecutor入口及完整重放验证该边界，不直接改manifest文件。

真实升级复核还发现恢复扫描自身的terminal_children集合只增不减，旧child失败仍屏蔽后续派发。按事件顺序遇到新的派发即移除该child旧终态标记，保留原有恢复次数与后续终态判断。增加完整“旧聚合失败→受控重试→新provider回合关闭”用例，避免仅证明manifest变pending就声称自愈完成。

精简失败示例的真实重试仍未完成：模型找到了真实Docker失败事件，却无法用局部文本patch替换大型scratch。最终复用已有`result validate --stdin --submit`，直接在briefing渲染同operation绑定的失败命令及紧凑JSON；worker填实际summary/evidence，不需要改成功模板，不新增草稿命令或权限。通过实际CLI解析、hook命令分类、preflight及submit证明失败准入且scratch保持原样。正式成功验收仍须完整产品报告；输入通道改变不绕过身份/lease/target门禁。


真实宿主交接进展：0491221e已使Terra通过stdin正式提交环境失败；RM自动运行build/test/browser三条冻结命令并恢复8个Candidate reader，原验收及已批准键盘补采均执行成功，尚待独立产品裁决。新reader的恢复上下文在19个必读来源中排第16，未进入briefing前8个导航；多名reader在读到宿主收据之前再次报告本地Docker拒绝。

补修恢复来源的优先导航：使用原artifact read能力和原source/artifact/path分页读取，在runner探测前呈现绑定恢复材料；正文不内联、不减少必读、不将宿主收据等同通过。真实briefing入口RED2 failed/1 passed，定向55 passed；增加Verify方法并同步三份skill。共享输入/恢复影响闭包待追加。原Candidate、Plan、sandbox和自动宿主白名单均保持原绑定。

恢复导航补修：定向55 passed；首次共享闭包在97%附近收到SIGTERM退出143，无完整结论，保留日志。以4进程重新完整运行同一选择闭包，3408 passed / 1 skipped / 1 failed；唯一失败仍为此前旧基线已复现的test_consumer_lock_contention_defers_without_reporting_failure，未修改其源码或掩盖失败。premerge sentinel53 passed。真实模型需加载新briefing后验证，整批保持active。


真实lane0仍等待的另一根因：旧代从未派发的request在环境等待失败后仍requested，后续角色释放及宿主绑定都被它阻挡。沿已接入runtime的reader terminal reconciliation补齐：仅Kernel环境等待失败、完整Run/Stage/Role/Attempt及不可变request中的fanout/child绑定、无任何启动/派发且无更新事件的requested才转机械failed；保留原失败causation，不编造语义结果、不改历史。实际runtime入口RED1 failed/12 passed；定向41 passed，影响闭包149 passed，sentinel53 passed。当前七个新reader已真实优先读取恢复上下文；第八槽位由operator受控暂停以补采精确命令，待验证补丁加载和官方restart。


收据交接补修：真实Candidate合同20条命令已有完整执行收据，但自动恢复只携带三条自动白名单结果，遗漏Owner批准的键盘补采及16条单项验证，模型再次准确报告未核验。补充仅收集同Run/Task/Map/目标/冻结合同与规范命令完全匹配、来源可信且manifest摘要和明确审批相符的operator host receipts；只传已有执行事实，不扩大自动执行命令、不授予产品通过。无收据列表改为全部规范命令，避免仅对白名单宣称“缺项为空”。实际账本只读复核17条补充均匹配。首次测试fixture的emit origin参数错误已修正，随后对原0cc06813重新运行有效回归复现；不把测试入口错误当作产品RED。

收据补修最终影响闭包141 passed、sentinel53 passed（含目标/合同/命令/代次/Run/审批/摘要/来源否定及失败收据保留）；原0cc06813有效回归12 failed，修复后通过。真实只读对账找到17条精确补充收据，仍须确认恢复后的模型输入实际携带并独立评估；未标产品或无人监工通过。


真实恢复再现Candidate宿主绑定拒绝：合法transport retry形成两条fanout dispatch，唯一派发数量检查误拒。改为同一operation已固定权威下的严格重试链：Kernel来源、previous run/cause、同fanout/child/stage/Run/role/ref、request hash、代次、精确目标和合同/目标快照refs/digests均匹配；未知派发仍拒绝。原始回归1 failed/16 passed；定向82 passed/2 deselected。实际失败operation在F源码只读preview已恢复到冻结1589e636，未执行任何新命令。等待最终闭包及实验载入。

重试绑定补修最终影响闭包见host-retry-lineage-closure.txt；sentinel53 passed、flow-smoke10 passed。实际恢复仍待模型与自动RM验证，原业务目标和必读要求保持。

## 合并时的真实验证边界（2026-11-18）

本批修复提交至 `4033786a` 已载入实验控制器。真实 Run `workflow-8625b886a7912513` 的16个业务Task已完成，Candidate `1589e63605ae7237c959a56961cdca51293ecd9e` 的20条规范命令均有同目标、exit 0、source_stable执行收据。07:11 UTC的自动恢复已把全部20条收据带入新reader输入，包含17条经Owner批准的补采；该交接现在已有真实调用链证据。

控制器已真实识别未提交结果的关闭回合，并沿同operation/session和分页cursor续跑。但07:33 UTC快照中最终Verify已有4路因续跑后仍未正式提交而失败，另4路未结束，尚无Candidate整体通过、Judge或交付完成记录。每路19个必读来源共596176字节，内容相同的两个别名属于可选输入，不是必读重复。正常长输入工作接力与故障重试预算、模型持续完成方法，以及role restart后的状态刷新仍需进一步验证和改进。

本轮包含监工补丁部署、空闲环境释放、17条命令补采和一次投影重建，不能称为无人监工闭环成功。真实业务阶段Impl峰值8、TaskVerify峰值6、合计峰值8，8+8同时运行0秒；最终8个Candidate reader不替代该容量证明。Planner/Run原有取消的时间及token限制保持不变。

本次合并交付已实施的修复，不把上述待验收项标成完成。任务保持active；原始事件、浏览器截图/WebM/trace和监工记录保留于实验worktree的 `reports/eight-by-eight-real-resumed-20261118/`，运行状态和私有凭据不进入Git。后续实际验证独立于源码合并继续评估。

合并前最终影响闭包：`python scripts/dev-verify.py run --base dev --workers 4 --keep-going` → 9832 passed / 16 skipped / 1 failed，耗时352.24秒；唯一失败为 `test_workflow_synthesis.py::test_consumer_lock_contention_defers_without_reporting_failure`。在干净的dev基线 `dda38a25` 独立执行同一用例，同样抛出WorkflowSynthesisConsumerBusy，确认本批未引入该失败；未修改该源码/测试或隐藏退出码。自动流程继续完成premerge sentinel 53 passed及flow-smoke 10 passed。此为影响闭包，不是全量pytest或真实产品验收；三套分发skill的metadata与源码一致性也已核对。

## Reader上下文重建与阶段工作记录

新Candidate replan从上一触发器恢复基础输入时，旧OA决定、delta及指令只作为历史观察，
当前请求显式携带的决定仍走原有身份校验。显式授权的新非OA stage代次同样隔离旧权限；
普通重试和合法OA请求不降级、不换基线。旧引用仍可按摘要读取，原始事件保留。
参见[前轮权限范围修复](../../tasks/done/2026-11-20-2325-replan-authority-context-scope.md)。

Candidate失败涉及已完成Task时，路径归属只能定位前驱，不能代替继任任务的输入消费审核。
若自动拼装的gap缺少前驱所要求的plan_input_selection，Run Manager在生成动作时即转入
既有semantic replan，交Agent审阅当前合同、失败证据及替代范围；不复制旧selection、不增加
attempt上限、不重开已完成Task。最终amend的消费合同门禁继续保留。
参见[替代输入审核修复](../../tasks/done/2026-11-20-2320-candidate-replacement-input-review.md)。

实际窗口发现必读齐全仍固定推荐reconstruct_required_inputs，压缩后反复全读。恢复投影用required_scope_count表示输入规模；historical_missing为0时交给Agent识别具体上下文缺口并继续，不再给出默认required批读命令。缺读、权限和输入版本门禁保留；历史不证明当前模型理解。Verifier在阅读完成和验收组完成时按既有WRC入口保存语义进度，恢复先读记录、按索引补正文，不用摘要替代首次必读或验收。

原WRC在同operation受控重派后可读：当前请求、manifest、角色、fanout、候选及有效lease仍须验证；旧记录必须有原生发布/派发/attempt证据，新的启动须绑定发布之后的kernel redrive准入。只允许传输dispatch/canonical attempt/lease变化，原记录不改写、不授予执行或通过。参见[上下文进度恢复任务](../../tasks/active/2026-11-20-2303-reader-context-progress-recovery.md)。

### 首次 Reader 尚未创建 call 的恢复

目标 pin 在 durable call 准备之前可能等待环境；若超时，不应要求一个从未存在的
operation ID。精确 `child-rebuild` 允许此唯一未准备情形省略 `target_operation_id`，
仍绑定 Run、fanout、child、原 pending run_id 与失败事件，并验证 Kernel 原始等待、
expected-child、无历史operation/派发、当前性和暂停边界。正常准备入口生成首次call，
以真实失败为因果，不补造前序身份。已有call仍须提供精确operation，不修改attempt上限。
RM建议、控制入口、执行及效果观察使用相同绑定；见
[首次Reader恢复任务](../../tasks/done/2026-11-21-0037-unprepared-reader-wait-recovery.md)。

### 控制器重启后的用量采样与活性

磁盘读取器重发计费事件时，新的事件时间不代表Provider产生了新活动。仅当
`usage_timestamp` 有效、不晚于观察时间且晚于已有心跳收据时，disk usage才刷新活性；
通用activity兜底不抢先刷新该来源。计费记录保留，普通工具/实时usage不受此检查影响。
这修复重放延期，不缩短既有失联阈值或长turn宽限。见
[磁盘用量活性任务](../../tasks/active/2026-11-21-0051-disk-usage-liveness-replay.md)。

### 同父任务 Reader 的失活恢复身份

多个 Candidate Reader 可以绑定同一父 Task。失活信号和恢复不能从父任务最后一次
派发推断当前 Reader 身份，否则较晚启动的 sibling 会让旧 Reader 永久 wait。
已具备 typed operation 的 Reader 按自身 fanout child/dispatch 绑定进入既有有限
respawn；在角色恢复锁内重检 operation、attempt、request hash、已准入结果和 Run
暂停/终态。恢复沿原 provider session、冻结输入和工作记录继续，不重排共享 Task，
也不把重新投递认定为产品通过。失败收据同样保留自身身份。
见[实施任务](../../tasks/active/2026-11-21-0108-reader-stuck-dispatch-identity.md)。

Reader transport重投可能更新外层TaskAttempt/dispatch，而嵌套payload保留原不可变call
attempt与request hash。恢复信号、读权限续期及读取指引使用原call身份，transport
收据仍指向最新派发；两层都必须与Kernel账本绑定。外/内operation、request hash、
Run冲突及source-manifest digest不匹配时拒绝，不能以一次新transport重试重置必读或WRC。
## Candidate超时交接

真实timeout聚合曾只列最后一个pending Reader，遗漏此前已准入的拒绝/abstention；OA因此无法对账失败集合。修复在既有timeout入口保留全部失败成员和finding，并按各失败kind合并；超时child事件补齐call身份。

旧账本不重写。Candidate恢复仅在精确kernel timeout因果、同fanout/child/role/transport→call、不可变请求与当前候选绑定成立时，补全遗漏成员。无result成员放入`unresolved_members`，保持outcome unknown；已准入结果仍留在`members`，不伪造control result、不授予通过。OA必须仍有至少一个有效语义拒绝作为主绑定；申请与apply均重核成员，后继只能进入既有Plan路径。执行/证据未知与产品语义结论分别由其owner处理。

任务：[Candidate超时交接](../../tasks/done/2026-11-21-0148-candidate-timeout-semantic-handoff.md)。

timeout兼容输入还须与kernel `fanout.started.expected_children`及role/stage闭合，每个预期成员都有终态；名单缺失、成员遗漏/额外/未决均拒绝。已有完成成员单列原事件事实，不生成产品verdict。原aggregate之后出现同fanout的新派发、终态或重开时旧checkpoint失效，申请与apply使用同一检查。旧显式infra timeout若同时含已准入negative，恢复分类经过完整候选绑定才得到mixed；绑定损坏保留unknown，避免宿主命令成功触发纯infra后继而掩盖反例。历史事件不修改。

## 媒体捕获与读取尺寸一致性

媒体投影沿用zf.yaml的host_verification.max_artifact_bytes/max_total_evidence_bytes，经过同一配置边界验证。Reader宿主恢复、OA失败输入、Candidate continuation均传入当前配置；无配置的独立调用保留20MiB/64MiB默认。先核验全部文件路径、base64、单文件/总量及digest，再发布只读投影和索引；不会重跑命令、改变原receipt或授予产品通过。真实23MB左右trace已在64MiB配置下合法捕获，读取端不能另用硬编码20MiB拒绝。

任务：[配置媒体读取上限](../../tasks/active/2026-11-21-0230-configured-media-projection-limits.md)。

## 历史preflight的usage按需输送

Planner(workflow-read)和Critic(plan-synth)的新call装配对完整授权、已知plan-candidate-preflight.v1中的execution-usage-facts.v1复用既有摘要/明细投影。新plan-preflight-delivery.v1明确标注原preflight的ref/digest及可检索source；仅usage groups成为按需明细，所有业务字段、错误、verification条款和未知字段原样保留。原文件不改写。部分授权和未知schema不自动扩张或缩减。

已有operation继续使用冻结输入：legacy全量输入不因升级被改变；已投影operation重放恢复其精确source集合，并校验原件与明细摘要。来源或授权变化仍进入request divergence检查。不能用新投影补写旧attempt的已读凭据，也不能把摘要视为业务验收。

原件、派生摘要和usage明细均保留原access_scope，未授权角色/用途仍拒绝；导入描述符不能自行声明kernel专属plan_preflight_delivery。各旧candidate使用独立source身份，避免与当前review的usage来源混淆。

任务：[历史preflight读取](../../tasks/active/2026-11-21-0244-previous-preflight-usage-delivery.md)。

## Candidate对既有host事实的独立消费

`reusable=false`限制Impl自检收据复用，不要求每个独立Reader重复已经合法完成的host命令。新Candidate call准备时，kernel从账本核验源operation完整身份、settled后执行的唯一因果链、审批、manifest、精确命令/合同/目标/世代、原收据和输出摘要，再冻结`candidate-host-evidence.v1`消费绑定。绑定包含新consumer身份和原source operation/attempt，纳入不可变request hash；已有call重放不补入事后事实。

绑定、带输出的fact和媒体索引进入必读manifest，原收据、原输出及完整容器证据仍可按需查询。派生源保留源权限交集；部分授权不扩大成全文。缺输出、媒体或坏摘要保留不可用事实，不能支持通过。图片、ZIP和视频按字节/长度/SHA验证，不能当文本解码。

独立Verify可在`host_execution_receipt_ids`引用本call冻结的receipt_id；既不填Impl的`reused_command_receipt_ids`，也不冒称自己的新probe。预检与正式准入重新核验原事件和证据，只有精确匹配且exit0的完整事实支持规范命令覆盖；AC矩阵、产品验收报告、读证明与Judge义务不变。新字段无合同、无consumer绑定、错身份或错目标均拒绝。读取事实不授予新命令、预算、镜像、网络或host权限。

任务：[Candidate host证据消费](../../tasks/active/2026-11-21-0300-candidate-host-evidence-consumption.md)。真实新Candidate派发前，聚焦测试通过仅证明确定性链路，不代表本轮8×8最终成功。


### 局部代次的 Candidate 逐 Task 证据（2026-11-21）

原生 freezer 已可汇集同 Run 不同 Task Map 代次的已集成任务，但 Candidate authority
默认按全局新代次检索全部 Verify，导致未重开任务报缺少当前 Verify。隔离真实 Git /
集成 / freezer 已复现；Verify 输入由测试提供，不是实际 Provider 完成证据。

修复由 Candidate 准备和结果 currentness 共用只读解析：以冻结事件前的逐 Task context、
完整 roster、不可变 integration refs 和原 Verify admission 为来源，并核对当前 Task
合同、完成状态、无活动 operation。此前经批准的 completion reuse 沿原 Candidate 与
完整 predecessor 链逐行验证；变化的兄弟任务不再被当成仍须满足旧合同的 Task。
不改写历史完成事件、旧目标或旧收据，不为新 Candidate 生成通过判定。

保留任务的旧/新 Plan Task Map 整行必须相同。沿用原 integration 校验，重新检查
TaskRef index、Git ref、rolling smoke 侧车与集成祖先；比较新候选与集成点之间的
已声明写入/读取路径。只读任务从相等且已核验的 Task Map 行读取
`verification_read_paths`，不能因 canonical `scope=[]` 而误拒，也不能把空集合
当作输入未变。缺失、变化或无法核验均返回结构化拒绝，走正常修复/重新验收。

新的 Candidate 仍绑定新 head、Plan package 和全局代次，并执行自己的命令、验收、
Judge/Goal/交付。隔离回归覆盖准备、实际结果提交入口、缺命令拒绝、真实 Git ref
变化、多轮复用链和只读输入；现场因 stage replan cap 等待 Owner，不能把隔离通过
写成真实恢复通过。见[实施任务](../../tasks/active/2026-11-21-0346-retained-candidate-task-authority.md)。

### 局部恢复 P0–P1 的入口修复（2026-11-21）

实施清单：[局部恢复收敛与诊断闭环](../../tasks/active/2026-11-21-0514-scoped-recovery-p0-p1.md)。

- 原 Planner 方法已要求保持范围外合同，但 preflight 没有对应拒绝项；Critic 才发现漂移。
  现在 validate、submit 和聚合共用的 preflight 从绑定的原 Plan 及请求选择计算保留集合，
  对同 Run、done、未刷新任务比较完整 Task Map 行。候选自行改 amend/选择不能豁免它们。
  这是完成证据一致性检查，不把执行选择当作编辑授权；全局输入变化仍走原审查。
  `previous_plan_candidate_refs` 中的被拒草稿不是权威基线。提交拒绝仍为 protocol repair，
  不消耗语义返工轮次。
- 原 OA Candidate 失败入口强制 `return_to_plan`，与 skill 的“普通缺陷走产品返工”不一致。
  现在已准入 OA 可在精确 Reader target 下提交 `rework` + `candidate_repair`，显式判断
  product_defect/合同不变，并提交标准 gap Tasks。窄路由只允许逐项复制原合同，改变新 ID、
  冻结 head 的 base 和单一前任关系，经已有 ControlledActionService/amend 派发后继；
  不重开 done Task、不授予 Reader 写权。blocked/unknown、错代次、改 AC/命令/范围、
  缺少全部受影响任务或次数耗尽均拒绝。语义义务改变继续使用有范围的 Plan 修订。
  canonical 行模式保留输入端口、entry、AC、命令及排除项；编译器只派生既有来源的复数别名、
  successor lineage 和命令 producer 身份，不通过 legacy 默认值改写合同。
  amendment/ready 以 checkpoint、Run、事件类型锁定，重放需完整 payload 和因果身份相等；
  在发布后、OA 回执前退出时复用既有事件，不重复派发，也不重复消耗同一修复的次数。
  若 ready 已发布，旧 Candidate 合法失效；恢复入口核验原 typed submit、Kernel admission、
  action start、amendment、ready 和侧车摘要，只补已发生动作的 OA 回执。此路径不重新执行
  旧 Candidate、不复活终态 Run；未发布 ready 的动作仍须经过当前版本与权限检查。
- 完整恢复测试发现 `test.failed` 的 registry 只把质量失败交给产品返工，技术失败没有
  RM pending action。现根据明确 `failure_kind=infra`、Candidate scope 和 fanout 身份
  生成诊断请求；Task 局部、产品拒绝和未知类型不据此生成技术重试或通过结论。
- Autoresearch 快速非零退出和轮询停止均读取所属子 Run 的事实；预算需要同 Run 的
  kernel budget 因果事件，保留停止事件、原因、WRC、下一 Owner 和退出码。其他停止保留
  unknown/child_run_stopped，不再统一丢成 runner_failed，也不擅自扩大预算/回答 Owner。

验证分层：定向测试覆盖实际 Plan validate/submit、OA 结果准入/受控应用与拒绝、历史
子退出观察；既有 Goal idle tick 验证仍有效，不另造监工循环。新增单账本 mock E2E
使用真实 Git 探测和结果准入，经过 post-verify Reader 执行失败 → RM 诊断动作 → 测试
Owner 明确批准已修输入 → 受控恢复 → 新控制器实例 → Reader → Thin Judge → Goal 收据。
Provider 输出和 Owner 批准是测试 actor；测试通过不代表真实 Provider 的自主诊断质量，
也不把最初失败的无监工 8×8 窗口改成通过。真实 Candidate/Judge/交付另记验收状态。

### 真实恢复补充：旧触发退役与旧式 Goal 草稿（2026-11-21）

真实恢复又复现两处缺口，不能仅凭上述隔离回归宣告恢复成功：

- `draft-baseline --seed-goals` 原来把显式 Goal 定义加入旧式 Task Map，
  但保留任务中的旧标签。派生 AC Goal 与旧标签不匹配，Planner 为通过校验而修改
  保留行。现在仅在不改变任何 Task 绑定、Goal coverage 合法时注入显式定义；
  否则保留 Task Map 原始字节，另提供只读参考 `baseline-goal-claims.json`。
  该参考不是 Plan 输出端口，原派生 Goal 连续性检查继续执行，不放宽合同保持规则。
- 已完成的 Owner Plan 新代次恢复，使同 Run/PDD 的更早 writer 意图失效。
  以前这类旧队列会先执行 package admission，失败后自动产生新的全范围 Plan，
  覆盖已经启动的局部恢复。现在在 writer admission 前核验既有受控恢复完整链，
  抑制旧触发并记 `fanout.retrigger.suppressed`，不制造新的产品失败。
  对已产生的错误 Plan，只沿同 Run/PDD 的原生 causation 追溯到旧 writer；
  reader 启动、聚合和 synth 结果入口均使用该事实。已启动分支通过现有 fanout
  cancellation 和 WorkflowOperationService 退役，历史结果仍保留，不成为新交付权限。
  其他 Run/PDD、未完成或外部伪造的恢复、恢复之后的新 writer 不因本规则失效。

恢复重试使用既有 `stage-retrigger` 重新发出被错误覆盖的原授权触发，保留其选择、
Plan 输入和 stage 代次；不重新授予预算或扩大任务集合。真实旧事件、后代和正确恢复
触发的只读回放分别验证“退役、退役、保持有效”；完整产品签收仍由后续正式结果证明。

晚到的自动重派也按原意图排序：复用 stall copy 的完整 payload/Run/Task 身份比较，
只承认 producer 声明的 metadata 和 task_id 回填；不能凭一个 original_trigger_event_id
退役不同的新工作。抑制事件逐 stage 绑定，并分别结算原触发和副本，使既有停滞检测器
看到已处理事实，不因缺少 stage_id 再次重派或把退休工作升级为新故障。

真实Planner随后暴露基线导出遗漏：`execution_spec`、`task_execution_input`及
`product_acceptance_spec`虽已绑定为Planner可读输入/可编写输出，却被CLI端口白名单拒绝，
增加重新构造未变规格的风险。补齐这三个已存在的输出端口，复用相同的Planner身份、
同Run/代次/Package、全文授权、ref/digest与私有草稿目录校验，逐字节导出。
未分配或不存在的端口继续拒绝；需求、审批和Goal权威不成为可编辑导出。复制不产生
阅读回执，也不证明语义修复或验收完成。
实际派发简报与CLI共用可编辑端口清单，仅把当前manifest已分配且全文可读的端口列入
导出命令；部分选择、未分配端口及需求/评审权威不会被简报扩成可编辑文件。

后续Critic通过却未提供可读取的Markdown路径时，Plan Preview可从同一ready事件的精确
Critic因果关系和reviewed generation，读取被审核合同绑定的唯一Planner报告及其Plan。
要求同Run/fanout、合同完整身份、同Task Map引用及摘要、作者/正文摘要；描述符如声明
局部选择则拒绝，沿用Operator Preview既有权限，不签发Worker读取能力。
缺失、歧义或篡改均不恢复。此路径支持Critic使用source_id:digest定位符，不搜索目录，
不借前轮Plan，也不修改Package、事件或审批；仅恢复已有正文的只读展示，Owner仍须审批。
检测到绑定的typed审核合同但正文恢复校验失败时，禁止继续旧的无摘要路径兜底；
没有typed合同的历史请求仍沿原有兼容入口。

### 宿主发布在新 Task Map 上的恢复（2026-11-21）

真实MEDIAN在新Map第1次Impl因Git索引只读提交blocked；旧Map已结束的第3次执行
被错误解释为当前占用者，Run Manager反复defer。宿主effect绑定现在仅把已结束、
Run/Map身份完整且不同的operation视为历史；当前范围的更高代次、任何活跃占用及
身份不完整的较高代次继续拒绝。当前Run、Task合同、唯一dispatch、路径与摘要核验不变。

类型化Impl结果的transport完成事件可以是`dev.build.done`，业务verdict仍是blocked。
重试入口同时接受它和旧式`dev.blocked`，但必须仍是当前已settled且已准入的
blocked/failed Impl，且canonical source精确属于该operation。宿主发布只生成一次
Git effect及其回执，后续仍由Worker复核提交和独立Verify裁决；不把发布当作验收通过。


### Owner 上调宿主采证额度后的接续

`runtime.host_verification.max_executions_per_run`接受显式非负整数；启用时必须大于0。
额度仍由Owner配置授权，Runtime不自动增额、不更换approval_ref重置计数。
既有`preauthorized_probe_budget_exhausted`仅是可按当前额度重判的等待事实；其他
escalation、未结算effect继续阻断。额度等待解除后产生的新失败必须独立记账。

容器回执保存完整policy_snapshot及其摘要。消费者只兼容额度增加且其他策略字段
全等的旧执行事实，缺字段/多字段/摘要不符拒绝。没有snapshot的历史回执最多匹配
旧parser允许的1..100；更早无采集上限字段的回执仍须证明留存字节满足当前上限。
所有Run、operation、冻结目标、命令和事件身份校验沿用原消费者，不由策略兼容替代。
宿主证据准备完成后仍经受控Reader恢复与独立语义验证，不能由执行器宣告产品通过。

现场记录与验证见[局部恢复实施任务](../../tasks/active/2026-11-21-0514-scoped-recovery-p0-p1.md)。


静默入口也必须重判已解除的预算等待：旧kernel/Run Manager预算告警宽限期结束
不能让tick提前返回并饿死已获授权的采证。仅当当前同Run/approval_ref尚有余量、
同manifest无其他hold且没有未结算effect，静默判断允许RM重新检查。此判断只唤醒
既有执行器，不签发权限或跳过operation/目标/合同校验；其他阻塞保持原有静默规则。


### Candidate Reader 重试保持原冻结身份

受控`stage-replan-new-generation`的Candidate infra重试创建新调度通知/operation，
引用原始`candidate_snapshot_event_id`；不会因为重试事件ID变化而改变验收合同或目标。
完整精确fanout来源保留空集合。历史复制native schema且漏两空数组的恢复通知，
只能通过同Run失败fanout、真实授权请求、已完成受控动作及全字段一致性回到原native。
不会改写旧事件、freeze receipt、Task完成证明或host receipt的source operation。

派发、输入/结果准入、成功消费及resume投影共用解析；真正新freeze仍使旧结果过期。
显式无效父ID不回退搜索；坏因果在resume投影表现为不可变更的阻塞checkpoint。
恢复投影必须用完整账本核授权因果，不能用仅筛选失败的事件视图作为证明。
Reader operation注册前已知执行失败记录infra，避免无依据进入产品质量返工。
新Reader独立消费现有采证，原request不修改；主机命令成功仍不是Reader/Judge通过。

## 2026-11-21：RM/OA 未完成监工闭环的实证复盘

11:19 `evt-b789c87eae0b`聚合为2路passed、6路blocked；全部Reader执行已完成，
失败分类dependency_blocked。浏览器命令已成功、7项原始产物齐全，但合同声明
`ZF_EVIDENCE_DIR/desktop.png`与索引`desktop.png`直接比较，被标为不可消费。
执行器只按当前operation查询完成回执，新Reader已绑定的19份事实未用于去重；
11:03–11:10重复10条命令，实际用量110→120，随后预算告警`evt-dbb5bc038b70`。

研究快照证明，11:19:44 RM已经有一个正式诊断待办，而非完全没有发现失败。
continuation选择器把采证预算告警作为整个Run的等待门，实际动作准入又重复拦截；
待办的workflow_rework路线也不符合resident RM诊断投递条件。11:21静默闸门进一步
停止tick。Candidate诊断另有低优先fallback隐患，但本次并非被其他workflow待办
遮蔽；修复/报告必须区分实际原因与额外回归覆盖。

```text
原链路：Reader blocked -> runtime重复采证 -> 额度耗尽
                          -> RM诊断在队列 -> 整Run等待门 -> 静默
修复后：Reader blocked -> 对账原效果 -> 输入改善证明 -> 受控Reader接续
                          | 无改善/未知原因
                          +-> 当前失败绑定的RM诊断 -> 有证据的修复建议
                                -> 原修复/验证/应用权限门 -> 原断点接续
```

实现约束：

- 声明输出根仅映射已知`ZF_EVIDENCE_DIR/`前缀；冻结和结果准入共用语义。
  不做任意环境展开或basename模糊匹配，原始合同、receipt和request保持不可变。
- 执行去重沿consumer冻结授权→原operation/request→完整effect/receipt验证，核
  Run/Task Map/目标/合同/ACL/policy/source/dependency；不以同命令字符串替代证明。
  已执行与媒体可消费分别判断；媒体交付错误不能驱动重新执行。
- 交接要求新回执或原来不可消费的输入确实修复。无变化时不再开新一轮Reader；
  未覆盖命令仍留下原升级记录，混合/语义失败仍留给语义Owner。
- 同Run原生宿主预算告警只限制新宿主执行。选择器和动作准入共用同一窄例外：
  核账本中的原生告警和当前Candidate infra失败，仅允许既有RM只读诊断。
  任意文字reason、Agent自报Owner、其他人工gate、暂停或变更动作都不能据此放行。
- 静默时只允许同Run、已证明输入改善、`allow_execution=False`的候选交接；
  当前诊断请求及其既有deadline/响应消费保持可达。不改预算、不清旧告警。

OA负责产品语义、合同范围及修复方案判断；本次应先由RM处理执行/输入交付故障。
不能把OA改为绕过所有门的第二状态机。当前source_repair仍为disabled、proposal_only、
never_during_active_run：即使诊断正确也没有自动源码发布权限，这是配置能力边界，
与此次诊断队列被挡分开记录。真正无人闭环仍需受控修复发布配置及独立零人工窗口证明。

实际只读预检：5路阻塞Reader可复用19/19原回执；lane1单独因dispatch retry身份
校验拒绝，未降级放行。当前聚合诊断可进入选择器与动作准入，原预算告警仍保留。
不得把这些预检、命令exit0或本轮人工补丁当作Reader/Judge/Goal正式签收。

监工验证也有遗漏：此前只检查了19份回执被绑定及摘要不变，没有检查每份
consumption_status，也没有覆盖“新Reader再次blocked后的真实reconcile”。本轮回归
增加请求冻结→结果准入→blocked后对账→输入变化才交接，并核实际tick和动作准入。
这比只增加监工角色或调高模型更直接地覆盖了此次故障。
# 2026-11-21：自动修复实验前的边界核验

Owner已批准原Run继续验收，同时补齐源码修复链，再开始新的零人工干预窗口。
跟踪任务：`tasks/active/2026-11-21-1235-autonomous-source-repair-qualification.md`。
当前代码与真实8×8签收分开记账；原窗口的人工介入事实保留。

- Reader真实新增失败：lane3传入的base64 cursor解码后bundle摘要漏字符，原拒绝正确；
  提供精确复制/当前会话仍可用时受控`--resume`的方法，不猜游标、不跳过必读。
  lane4在正常事件持续append时两次前缀复核失效，已用独立进程复现；仅在前缀证明
  期间取同一EventLog写锁，不在解码/工件读取期间锁全账本，继续保留改写与轮转拒绝。
- 独立源码验证现在检查实际Git共同仓库、精确HEAD/base/单提交、干净索引/工作区、
  实际完整差异与冻结scope，验证前后均核对并记录tested_candidate。应用入口同时
  要求这份身份绑定；旧的仅有合同摘要的validation不能充当新的自动应用证明。
- 缺少重启项目目录在应用前阻断。已应用但actuator尚未启动且spawn明确失败时，
  仅对仍是原应用HEAD、干净且父提交正确的checkout执行保留历史的revert；有并发
  编辑则拒绝回滚并保留needs_review。未知启动结果不擅自revert或再次cherry-pick。
- 原检查点只在最新`loop.started`的runtime-load侧车验证状态目录、源码根、提交和
  Python源码树摘要均匹配后恢复。仅`session.started`、旧版本、缺失/损坏侧车、
  后续stopped或未知boot均不能证明加载成功。merging/等待重启超过120秒产出幂等
  RM只读诊断动作；不是新的自动修复授权，也不声称进程永远存活。
- 修复provider在dispatcher统一消费source_repair的显式backend/model/effort，
  缺省保留调用方fallback。watchdog与兼容推荐生产者保留结构化验证命令；不把
  verification prose解析成shell。dev-verify使用当前可信解释器和候选cwd；
  必需人工文档审查缺证时失败，不把“记录了审查指令”标为passed。
- resident接管修复时使用`zf self-repair run --accepted-only`，与正常RM消费同一
  `run.manager.repair.accepted`事实；legacy/manual默认仍消费原autoresearch请求。
  实际CLI→dispatcher测试使用真实临时Git，只替换provider进程，证明accepted-only
  请求产生一次派发，未接受请求不混入，重放无重复。此前仅mock命令返回0漏掉该断链。
- 审计文件只有同时匹配冻结governance_scope和Owner allow_paths才能发布。
  默认授权不扩大。ignored的新backlog原本不参与publisher枚举，因此此前“必然
  被审计文件挡住”的调研猜测已撤回；本次只对显式纳入候选的审计文件统一边界。

尚待真实证明：source_repair仍关闭，自动发布/重启没有据以上单测冒充已启用。
需完成隔离纵向验证及配置预检，再部署当前Reader修复，观察真实Reader/Judge/Goal/
本地交付。新无监工资格窗口只有满足前置条件后才启动。120次原采证额度不变。


13:00部署与后续实测：01acdbdb/f3dbea84已加载，控制面重启保留全部worker pane；
配置与120采证额度不变。旧代码运行的Reader最终6通过、2执行失败，未进Judge。
lane3自动retry生成了新candidate-host consumer摘要，却继承旧required_reads，形成
悬空必读。新retry准备仅去掉同Reader不可变祖先清单可证明的candidate-host-execution派生条目，
通过原调用路径从新清单重建；保留自定义选段与严格限制，既有失败请求/读回执不改。
首次与多代retry均验证全部必读身份可解析，宿主执行不重复。原窗口仍非无人通过。
RM正确诊断之后遇到全Run进度前置误过期，当前单独跟进，不以本读取修复声称解决。


RM故障定位与处理：原建议evt-d8e74e6c5787的Reader目标仍当前，唯全Run进度摘要变化，
致建议被误判过期。经kernel诊断请求中的确切故障事件证明后，回复前置收窄为该
Reader的operation/request/attempt/结果、Run代际及Goal；兄弟lane进展不再使其失效。
原请求、角色、失败事件或目标不匹配仍用原拒绝路径；不增加Agent修复权限。
旧建议已superseded而案卷停留diagnosing时，仅严格更新的kernel infra失败可重新分诊。
必须核原请求、指定响应者及kernel过期消费事实；未完成请求/人工等待不解除，
变更动作不走此例外。该处理保留旧失败和旧建议，不把空队列解释为完成。


旧式role-v1 TaskAttempt失败没有call request_hash。对它核kernel settlement回执与
原admitted_call_result_ref、source_event_id和dispatch绑定，再采用Reader范围前置；
不猜测或把ta-*当作call attempt。实账本原快照通过、已有新尝试后的旧目标拒绝。
独立研究重点复核diagnose到resident reprompt的二次准入，最后335项闭包和原有人工门
均通过。完整自动闭包5984/7skip，加最后格式/实际派发形态增量验证，非全仓pytest。


### Reader missing-reads 与提交门禁声明统一（2026-11-21）

真实lane3重试先收到“missing_reads=[]”，提交时却被退回30项必读。
回放确认query旧路径只查事件内联required_reads，Reader声明实际在不可变request→policy
sidecar中：旧查询required=0/missing=0，canonical required=40/missing=30。
服务复用required_reads_for_attempt，通过唯一operation/attempt定位并校验sidecar；
保留原有任务内联声明兼容。缺失、错误attempt、摘要损坏或歧义声明为unknown，
missing_reads和required_read_count为null、protocol_repair_required=true，不能当全部读完。
CLI与Web端使用同一服务。真正完整读取后才返回missing=[]。不修改门禁、消费账本或语义结果。
4项CLI回归RED，加入Web一致性检查；真实回放与完整影响闭包随后记录。


RC-QUERY最终验证：188项影响闭包（含CLI/Web入口与mock E2E）、sentinel53、
flow smoke10通过。测试环境曾缺httpx；补齐项目dev/web依赖后重跑sentinel通过，
没有把环境启动错误计作产品RED。独立只读review未发现阻断项。
真实被拒绝sealed ledger摘要3d133cf3119088ee…回放：新查询required40/missing30，
与提交门禁一致；旧查询0/0。未写业务状态或消费凭证。

旧Run在13:59:52由lane3自动修正后正式passed；8/8 Reader通过，13:59:54产生
`test.passed evt-059b07206036`，14:00自动进入post-verify discovery。
这次补读与重新提交没有人工代交，之前的整轮人工介入记录仍保留，整体无人未通过。

新Run规划阶段13:51失败：Planner未产出必需Plan且失败报告recommendation枚举不一致。
RM在13:54自动诊断并接受修复evt-5fba52d8c454，已派发Luna隔离源码修复
（/tmp/zf-self-repair/self-repair_recommendation-evt-1d0ac4bd0779-e890eeeb6f07e70a-a0），
同时在原Run下发起缺失Plan语义诊断。当前只观察，不与其并发改动新harness HEAD；
独立验证、应用与恢复仍待真实结果。Plan尚未批准，8×8资格窗口仍未开始。


### 独立源码验证命令兼容修复（2026-11-21）

新Run的原生Luna修复在14:02:37完成，14:04:19由受控publisher发布候选1d39f418；
独立验证evt-77004db6fefb失败于command_not_allowlisted。冻结合同中的
`PYTHONPATH=src uv run pytest ...`未被验证器接受，未运行合同测试，未应用候选。
仅将已知pytest写法归一为可信解释器`sys.executable -m pytest`，保持候选cwd与
PYTHONPATH=src；只接受既有固定环境值，不执行候选venv入口、不扩大shell/任意环境权限。
冻结合同原文和原失败记录保留。非法引号也返回拒绝，不使验证器异常退出。
3项真实RED后，252项影响闭包、53项sentinel、3项flow smoke测试通过；
原生候选另由监工独立运行原合同96项通过，不等同正式自动验证receipt或自动应用。

新发现SR-FAIL-FEEDBACK：失败队列进入needs_review后没有待执行动作，RM observing；
14:16投影pending_actions=0、repair_closeout_pending。medium风险旧展示并非实际权限
阻断：router在显式verified_checkpoint_apply授权及绑定满足时允许medium。
下一步补失败到只读诊断路径并重新验证，不能用解析兼容修复声称运行已恢复。
旧Run仍在post-verify discovery；新Run Plan未批准，资格窗口未开始。

命令兼容最终自动影响闭包（dev-verify基线23782907）：1831 passed/7 skipped，
sentinel 53 passed，flow mock 10 passed。没有全仓或真实Provider验收冒充。
原生候选独立影响闭包539 passed；仍没有原生重验/应用receipt。


SR-FAIL-FEEDBACK最小修复：`needs_review + validation.failed`现在派生一次只读RM诊断，
checkpoint按失败验证event ID去重，携带原Run、queue/candidate、Git base/commit和合同
摘要。保持失败队列、候选与原验证结果，不自动改成通过，不盲重试或自动rebase。
已合并/丢弃候选不再开案。真实账本回放从0动作变成一个可派发diagnose-attention，
精确绑定evt-77004db6fefb；该回放不写业务事件。1项RED后251项直接调用闭包通过，
包含实际run_manager_tick派发诊断、重放去重和不产生apply/restart的断言。
独立只读研究确认部署后旧候选会base漂移，必须保留旧失败，通过Agent新合同产生
新候选；不将旧候选强行应用到新基线。controlled重验尚未从Agent输入完整接通，
属于已记录后续缺口，不能把当前转诊修复称作完整自动修复闭环。

SR-FAIL-FEEDBACK最终自动影响闭包：5931 passed/7 skipped（184秒），sentinel53、
flow mock10通过；resident实际二次准入形态另3项通过。准备部署新隔离harness，
保留旧候选及其失败，不动dev、不push。当前任务仍active，真实自动应用/恢复未证明。


2026-11-21 14:24:45：新实验harness已加载d1b173b7，loop.started evt-d1a15de78209
与runtime-load receipt一致，配置sha fadd068e…不变，全部worker pane/PID保留。
14:21 Anchor的1800秒orphan计时升级产生evt-dd6422696838，叠加等待门。14:28监工
依既有继续恢复授权，通过controlled human-decision-dismiss确认该计时告警，记录
operator.intent evt-3e382fb5e1a5；未删失败、未改任务结果，属于准备期人工介入。
14:29:55 runtime原生派发新的验证失败诊断evt-edb724d5b0ba（request rmd-d1244b1228c8ddfa）；
14:33:47 RM返回evt-bf72aa9523ef，完整保留旧候选/旧失败，指出controlled重验入口缺失。
因此已证明失败会回到RM，但尚未证明自动完成修复。

新增两个复现与修复：
- SR-SELECT：Autoresearch单飞槽忙时，普通attention占队首，空闲resident的后续诊断
  被饿死；原逻辑只对no_progress特例让行。改为按实际可用执行角色选择：保留旧诊断，
  优先已有合法可派发的resident/OA动作；人工门、pause、case和去重仍检查。
  1项RED，292项定向调用闭包通过；独立只读review未发现放宽权限。
- SR-CONT：原合同continuation的agent_recommendation是建议身份，不是workflow
  checkpoint。真实Git apply+mock restart复现后，未知/缺失恢复动作改为绑定合入与
  load receipt后的RM新诊断，原case保留在continuation，新诊断不复用已结束动作身份。
  没有匹配新load receipt仍不恢复；1项RED，268项调用闭包通过。

后续已确认能力缺口仍在本active批次：
- [ ] SR-REVALIDATE：Agent在既有授权内请求重验 -> kernel依据queue/候选/原失败/冻结
  合同重建验证动作，不能信任Agent重新传入的路径/命令/required标志；保留旧失败，
  有界的新验证记录通过后才回到apply检查。
- [ ] SR-DISPOSITION：旧候选因基线变化不可直接应用 -> 受控记录替代/丢弃关系，
  保留提交与失败，不以新候选存在就关闭原业务故障。
- [ ] SR-POLICY：medium风险和实际预授权apply准入展示一致，避免把已授权恢复误写为
  必须再次请Owner授权；真正缺失执行入口应报告能力缺口，不冒充权限限制。

新资格窗口仍未开始，旧Run42项post-verify必读已读齐，正式discovery/Judge/Goal待结果。

SR-SELECT/SR-CONT最终自动影响闭包：5944 passed/7 skipped，sentinel53、flow mock10
通过；独立只读review未发现阻断项。新Run的RM当前已返回human请求，因为缺少受控
重验入口；这不是新授权自动出现。下一步补kernel重建入口及当前基线漂移检查，
不让Agent重传命令/合同、不靠不断换失败ID重置次数。每候选+冻结合同+可信验证器
版本最多一次，失败ID仅校验当前性。旧失败与新结果均留账。


SR-REVALIDATE实现记录：复用repair-closeout-validate，已同时接入真实RM recommendation
消费与ControlledActionService；Agent/操作员只给queue_id、原失败event ID和合同digest，
kernel重建原worktree/base/commit/命令/required合同，忽略调用者传入的替代计划或路径。
仍检查source_repair启用、当前Owner路径范围、原失败当前性、pause/human/case门、
冻结合同与候选前后Git身份。每候选+合同+可信验证器源码digest仅一次；更换失败event
不能重置次数。原始失败保留，新结果携带previous_validation_event_id和validator_digest。
只有绑定成功的新结果才能使needs_review回到closeout_required，错误来源/旧候选/
旧失败/merged/discarded不恢复。活跃harness HEAD偏离冻结base时，验证前即明确失败
repair_validation_live_base_changed，避免用旧候选测试通过冒充可应用的新候选。
原生RM入口另有RED，已验证重复tick只执行一次且不采用Agent注入命令。
这仍不等同真实自动应用或最终无人验收通过。

旧Run补充：post-verify discovery已正式passed并进入最终Judge；14:35与14:42两次
提交被child_authorities的contract_revision/task_map_generation当前性门拒绝，
14:45:10 operation失败evt-91d70f6f1c21（output_repair_exhausted）。原16业务任务和
8Reader结果未改，不能把Judge的尝试算通过。独立研究正在对照不可变输入与门禁，
核查旧权限版本来自Agent输出还是派发上下文绑定。


2026-11-21 15:02 UTC复核补充：SR-REVALIDATE首轮自动影响闭包6397 passed/9 skipped、
sentinel53、flow mock10通过；独立只读review发现RM入口覆盖了当前诊断的recovery_case_id。
已用真实tick复现：原诊断case为blocked却执行验证。修复后保留已绑定诊断case，
Run/Task与冻结continuation矛盾即拒绝；操作员入口仍只采信原失败case。
新测试先证明case_blocked，再验证命令执行次数为0；相关闭包107 passed。

SR-JUDGE-REUSE：旧Run的42条拒绝来自14项已审核复用任务各3项身份。Candidate准入
已验证复用链并比较prior_identity，但Thin Judge直接与当前Task身份比较；第二次
Agent修改输出child_authorities没有作用，因为门禁读取不可变snapshot。
已抽取共用child authority校验；Judge先绑定当前冻结Candidate的Run/目标/generation/
Plan package，再核验完整复用证明、原Verify/整合及当前合同，普通任务继续校验当前身份。
不修改快照或原生结果，不以存在reuse字段放行。相关直接调用测试66 passed；
旧Run原始b8f5f2e1提交只读回放从42条错误变为0，尚未正式重试，不代表Judge通过。
篡改证明、当前合同、普通任务合同、Candidate目标、generation或原Verify ID均拒绝。
SR-REVALIDATE+SR-JUDGE-REUSE最终自动影响闭包与部署结果待记录；任务保持active。

最终review收口：RM仅继承已成功绑定的durable request原字段；无request或无case的
request不得用自报case绕开原失败门。相关109测试通过。Judge另外绑定准确closure
fact ref/digest和snapshot，从其kernel-admitted Verify追溯原native Candidate event、
freeze ref/digest及完整child authorities；新freeze同内容不能偷用旧验收，相同snapshot
的新closure有独立回归。Judge最终直接调用闭包68通过。独立只读review无剩余阻塞。
旧原生Judge的语义意见为blocked：产品旅程通过，但8+8真实重叠、自恢复、零介入证据
不足；本轮只修机械误拒，恢复后仍由Judge如实裁决，不能把旧窗口改判无监工成功。

本批自动影响闭包6562 passed/9 skipped，sentinel53、flow mock10通过；自动测试采集后
补充的closure pin回归及最终helper另跑直接调用闭包68 passed，恢复入口最终109 passed。
原始Judge结果完整_goal_closure_issues只读回放为0问题，其语义blocked原样保留。
以上是确定性/模拟与历史证据回放，不是新的real-provider无监工资格通过。待真实恢复。


15:12–15:15 UTC真实部署与恢复：实现提交539bd829（fix: preserve recovery and retained
verification authority）已分别fast-forward到两个实验harness分支，未合dev/未push。
两次runtime-load均证明源码539bd829；配置摘要不变、全部Worker pane/PID保留。
旧Run按既有Owner授权解除本次修复请求等待，精确恢复Judge，原生派发evt-0679d912c398，
新operation为wop-prd-lanes-final-judge-prd-trig-evt-60082029-retr-bf26469e5409。
Judge新会话启动Luna，按既有授权在tmux切换6.1 Sol high并确认；单列人工模型介入，
未把它计入无监工窗口。旧业务/Reader证据保留，最终语义裁决待正式回执。

新Run首次受控重验遇到human解除尚未进入当前投影，门禁拒绝且未执行命令。
只读确认gate为空、case admitted后才再次调用；evt-131cc16f41e8以
repair_validation_live_base_changed失败，results为空，冻结候选未应用。
原候选1d39f418/旧合同/evt-77004db6fefb原失败均保留；当前基线不允许强行rebase/apply。
RM已经收到另一条恢复诊断，新增验证失败应由调度继续交给原生RM。
资格窗口仍未开始；SR-DISPOSITION与真实自动apply→restart→resume仍未签收。


15:27–15:34 UTC原生恢复实测：RM自行产生两项repair建议并派发Luna workers。
原枚举修复候选22630368由独立验证evt-30e3183682a9通过，runtime应用为3d9b917f，
merge回执evt-944b7e8dd7ab；systemd自行重启，evt-fd1659b0e696的runtime-load匹配
applied commit/source tree/state，repaired_runtime_observed=True。根会话未代替本轮
生成候选、应用或启动重启。原业务Planner尚未恢复，不能将四步当作五步闭环通过。
CLI包装事件注册候选dfb5f95c受并行前一项应用影响，验证拒绝live_base_changed，保留失败。
另有watchdog候选b3e965a4验证通过，但缺少continuation，保持应用门拒绝。

发现SR-WAKE：15:27:12 repair-closeout-validate失败后，post-repair诊断和新失败诊断
均可派发、门禁为空，但self-event被NOOP过滤且不在快速恢复输入集合，实际到15:32:17
才由300秒Supervisor周期唤醒。4条真实tick入口RED已复现；仅将kernel-origin、带
queue+contract绑定的repair-closeout-validate/apply成功或失败收据作为新输入。普通
bookkeeping、blocked、未绑定或worker事件不快唤醒；cursor消费后不重复循环。

SR-STRICT：strict repair_contract_required队列原来可因human确认/普通业务进展消失，
且terminal_run_passed可绕过它。修复只针对strict非merged/discarded行；legacy不变。
初始fixture缺resume_original_workflow已更正，对642ec609实跑RED为3失败/3通过。
两个修复的直接调用及相关mock闭包236通过，独立review和自动影响闭包待完成。
当前还存在活跃原生repair worker，暂不部署根会话更新去改变其冻结基线。

15:40 UTC：SR-WAKE/SR-STRICT 自动影响闭包6149 passed/7 skipped，sentinel53、flow mock10通过；独立只读review未发现阻断项，确认cursor取执行前快照，不吞掉执行中产生的后继回执。仍不代表原业务恢复或无监工资格通过。原生第三个repair worker已终结，候选6aea45e2同样因live_base_changed被拒绝，尚未应用。


15:41–15:56 UTC增量：559f9f00提交SR-WAKE/STRICT；将原生应用3d9b917f并入本隔离修复分支，合并提交604b25ab（未合dev/未push），38项交叉测试通过。NEW于15:41:14受控部署604b25ab，停前/停后均无活跃源码repair worker，全部Worker pane/PID保持，配置摘要fadd068e…与120额度不变；备份before-repair-wake-deploy。此部署是准备期人工介入。

RM原生建议evt-3ecdc71f434a选择精确从已完成Scan evt-35aeed9fff80重触发缺失prd-plan。原恢复尚需owner审批，不宣称五步无人闭环。按既有授权经共享审批服务提交evt-a36f394ab063，仅批准hdec-049cf7fbfe66；未批准尚未生成的Plan，另一个side failure-closeout审批不动。15:46:24内核stage-retrigger applied evt-ba56da6bcd1d，15:46:29请求新Planner wop-prd-plan-planner-trig-evt-3ca27989-rework-af2db023aeda，实际running。8×8资格仍未开始。

SR-WAKE补充：上述审批15:42:04→15:46:24才消费，真实入口RED证明human.escalation.acknowledged不在快速输入集合。将其作为唤醒输入，审批自身的token/当前性/授权准入不变。
SR-DISPOSITION：新增受控动作repair-closeout-dispose，仅允许严格failed旧候选由已admitted、验证且应用的明确关联替代候选接替。CAS queue/合同/当前失败，核对替代请求原失败链、合同、验证、apply及Run/Task范围。只发append-only处置，不改Git、不删除旧证据、不恢复业务。原请求case准入后另查独立处置case，处置回执不改变原案卷。无此入口RED7失败/11通过；直接回归287通过后，独立review指出resident案卷覆盖，已收窄并强化不变性断言。兼容旧operator验证未设origin只允许完整external intent→同绑定RM planned→failed链，worker仍拒绝；实际旧evt-131cc16f41e8只读预检可通过，未执行收尾。
SR-POLICY：medium路径风险只给policy_evaluation_required，审批需求未知；high原审批提示保持，实际source-repair准入未改。
SR-BOOT：原生3d9b917f在15:27有精确boot证明；root升级604b25ab后15:46又误报原repair_restart_outcome_unknown。历史证明与当前resume门分离，历史精确证明只用于分类repair_runtime_binding_changed，不放行当前未知版本。RED1失败/3通过。未篡改旧事件或把根会话升级算作原生自恢复。

16:10 UTC：补齐SR-DISPOSITION历史链的前次失败/source commit绑定与双case拒绝回归；测试fixture曾把首次失败误标为重验，现通过真实ControlledActionService生成合法失败重验链，不把fixture错误记为生产缺陷。最终直接调用闭包341 passed。首轮自动闭包在补用例时出现xdist收集不一致，7个collection error，该轮无效；冻结候选后重跑，禁止以此宣称验证通过。

SR-FREEZE：原生CLI候选续作evt-3e61598caa20多次因repair_contract_freeze_failed拒绝，原因是当前可信sys.executable位于harness目录，被字符串检查误当旧工作区测试目标。保留全部失败，先识别受控pytest命令，仅豁免可信解释器argv[0]，其余测试/config参数仍拒绝原目录，未知解释器与shell形式保持原安全检查。真实冻结入口RED1失败/1通过，修复后直接闭包包含此项。

NEW第二项原生修复（Supervisor已dismissed attention投影）已自行应用为f5eb24e7并重启，根会话未代替该项应用；待核对独立收据、保留到根修复分支。Planner首轮重触发passed后，合成评审16:07 rejected：必读bundle缺精确docs/prd/TODO.md与SOURCE-PROMPT.md，不能凭Planner摘要独立审阅。运行时已自动派发下一Planner代际，尚未解决此authority-input缺口，不能批准Plan或宣称无人8×8开始。

16:22 UTC：冻结提交c7e403ac加原生f5eb24e7整合为4ab2e751，自动影响闭包7143 passed/9 skipped。随后sentinel52通过/1失败，定位原生Supervisor修复使supervisor_attention.py超过1000行；未绕过哨兵、未将失败当通过。沿同一职责提取supervisor_human_decisions.py（79行），原文件990行，行为不变。最终增量自动闭包111 passed、sentinel53、flow mock10通过。7143是在提取前的冻结候选运行，提取后的证据是111+53+10，不称为全pytest或真实无人E2E。

旧Run最终Judge15:57:15正式settled/blocked，准入evt-7bf557eee79e、结果bf040c32…，69必读全部完成。产品验收passed，19目标声明closed、1 blocked；明确未获得16任务Plan的准确审批（evt-1695fe7d4282）。此外监工仍保留原无人窗口失败、真实8+8重叠和故障恢复未证明，不能因后来审批或本次机械门修复宣称原轮无人通过。旧Run尚无Goal完成或本地交付通过。

新Plan第二轮16:17:08正式abstained，missing primary authority bodies仍在；RM16:17:13自行进入语义诊断rmd-ffb913102f3bd820。只读研究确认：trigger artifact_refs与Task spec已声明TODO但plan handoff未消费；SOURCE-PROMPT只在正文引用中，未显式声明为原文输入。应由上游声明并冻结两份正文再进入新审阅输入；不递归猜链接，不靠重复Planner改文案掩盖输入缺失。此后续项尚未修复。

16:23 UTC f2e927da已部署新隔离控制面。显式处置evt-1a6b5db55281关闭旧枚举失败候选，
保留原失败和案卷；约2秒后RM动作，快速唤醒真实生效。CLI续作自动派发，但其独立
影响验证在16:30因最终Writer briefing 12681字节超过12288失败（2751通过），没有应用。
RM随后自行诊断并派发新隔离续作，不放宽长度检查或改写旧候选。

Plan正文传递修复增加以下通用合同：
- Plan handoff从已绑定的requirement-spec.v1读取明确source_refs（含Task spec），以及
  显式requirement_authority/source_document输入；将正文冻结进入当前Plan输入和必读。
  普通artifact_refs、中间矩阵和Markdown链接不被批量提升为需求权威。
- 已有digest必须匹配；缺失的明确本地正文拒绝。access_scope和allowed_paths保持，
  不因为成为必读就扩大访问或选段。当前需求及合法owner amendment的语义优先级不变。
- stage-retrigger可携带supplemental_input_refs，每项必须是已存在且sha256匹配的state
  sidecar；受控动作绑定请求来源，仅追加新trigger输入。RM建议归一/转发保留此字段，
  身份冲突拒绝。原Request、旧attempt、失败和审批门不变；反馈中的路径不会被自动读取。

回归先8项RED；首个收窄候选自动影响闭包3216 passed/2 skipped、sentinel53通过。
独立review提出allowed_paths保持和RM字段转发缺口，已补回归；最终验证另记。
真实Plan manifest在隔离状态目录回放，原版本TODO正文0份，新候选1份且必读；
SOURCE-PROMPT仍需通过受控补充明确交付，不能宣称现有Run已恢复或完整正文已到达。

最终Plan修复核验：重复同ref+digest但读取限制冲突时拒绝，不选择较宽声明；畸形
选段及scope限制拒绝，明确Task spec摘要校验。独立复核无阻断。冻结版自动影响闭包
6150 passed/9 skipped、sentinel53、flow10；最后纯验证分支增补后直接110、sentinel53、
flow10通过。完整受控恢复到新Plan输入/必读的回归已覆盖；尚未部署，不等同真实恢复。

16:41:51 OLD当前完整Plan准确批准evt-fd09060d18a6，保留原无人失败。其后审批重放
重开14个历史done Task，并把terminal_tasks拒绝错误路由为schema_gap重新规划。
16:44:47受控暂停OLD evt-67ef2172796f，NEW继续。旧审批去重又被本次fanout.cancelled
排除，旧同图trigger重新请求审批；需要修复完成状态保护及恢复路由，未重写历史。

SR-ENV真实事故：原生候选b7f3334a/f507a4eb独立验证继承controller的ZF_PROJECT_ROOT、
ZF_STATE_DIR、ZF_WORKSPACE_HOME等。16:46:52测试force-init清空NEW原账本及Kanban，
16:51观察主账本从3067条降为66条、anchor被5个测试Task替代。配置摘要fadd068e保持。
控制器、相关4个私有tmux会话及autoresearch sidecar已停止；事故文件保全到独立报告目录。
16:22:19检查点含2818条原始事件；索引另124条覆盖前观察不等于完整权威账本，禁止
直接把投影提升为恢复真相。后续恢复必须标明检查点和未证明的差量，不能声明无损或无人成功。

验证器修复：实际子进程剥离继承的ZF_/GIT_/TMUX及Python/pytest上下文，为每份验证计划
创建独立临时workspace，并固定可信解释器和候选src；计划结束自动清理。实际pytest子进程
回归先RED后GREEN，证明运行项目定位/控制凭证不继承，写入隔离workspace且测试后删除。
直接调用42、自动影响闭包1823 passed/7 skipped、sentinel53、flow mock10通过。
一次人工focused命令含不存在的测试文件，未执行测试；已更正，不算产品失败或通过。

源码修复进程治理：Worker与RM独立验证通过同一私有Linux subreaper执行命令。
正常结束、命令超时、调用Worker死亡均先清理后代再报告终态；独立setsid/double-fork
后代仍被收养。只枚举直接子进程，父进程退出后逐轮收养其后代；pidfd加PPid/出生时间复核限定信号目标，Python未编译pidfd时使用libc
同名接口，无法建立能力时不执行命令。直接子进程表与后代表均空才证明清理完成。
清理收据随Worker结果或验证step持久化，unknown不能成为成功，optional step同样
阻止验证通过及后续命令。该机制不是文件系统sandbox；supervisor自身丢失/超时保持
unknown，不宣称强杀宿主或不可中断内核任务后仍能完全清理。

聚合恢复请求不能只依赖首次事件消费。已准入的kernel-origin aggregate rebuild若因
Run临时blocked被原reducer拒绝，idle replay及Run重新active/resumed时从账本重查
未完成请求；项目暂停、未知Run、取消/最终终态仍不执行。继续复用原source/manifest/
Candidate currentness/子项终态校验及完成去重，不再开Worker或发新审批。

重建聚合的发布使用精确outbox：首次生成完整事件并冻结sidecar后，才追加引用它的
aggregate.completed；正常发布、原reducer再次调用和周期恢复共用同一body、event ID与
因果链。冻结后、aggregate后、publication后三断点均可恢复且不重复聚合/派发。校验器
拒绝事件明确origin=kernel；只有精确ID/type/body的kernel拒绝能停止重发，不能当成功。
取消fanout和失效Candidate继续拒绝；native还比较原冻结身份，不能仅以同head复用。
partial_task_continuation明确绑定当前baseline事件及自身continuation ref/commit，不能
误当已冻结Candidate，也不能跨baseline重放。旧complete缺outbox仍保持unknown。

原请求带source_event_id时，恢复还必须核对该精确、更早、kernel同Run证据所绑定的
冻结版本；不能用fanout启动时才出现的新freeze替换它。相同绑定上的后续test.passed
不使原准入决定失效。最终199项直接调用及sentinel53/flow10通过；更早冻结版自动
11697通过/10跳过、serial4，不等同真实无监工资格通过。

历史前缀决定Candidate选择；受控repair副本的授权归一可使用完整账本中较晚的动作
完成回执，但不能重新选择最新Candidate。真实controlled repair两种副本正例与新
freeze反例通过。最终252直接调用/候选恢复mock、sentinel53、flow10通过，独立复核
无剩余确定阻断。
