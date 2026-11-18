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
