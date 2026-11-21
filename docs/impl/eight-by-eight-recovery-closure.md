# 8×8 执行与恢复链路修复

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
