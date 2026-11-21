# 8×8 局部恢复收敛与诊断闭环 P0–P1

> 状态: active

Owner 已明确授权“好，执行p0-p1修复”。基于隔离分支49d343e0实施已讨论的五项；先复现、最小修复、影响闭包、提交，随后在既有授权范围内受控真实复测。原无监工窗口失败保持；不合ZaoFu dev、不push、不修改canonical JSON、不代Worker提交结果。

| ID | 优先级 | 工作与验收 | 状态 |
|---|---|---|---|
| P0-1 | P0 | 局部Planner返工基线及范围约束 -> verify: 实际提交预检在Critic前拒绝范围外合同/命令/责任漂移，合法局部变化通过；范围来自Agent已准入决定，Runtime只核绑定/差量。 | 已实现并通过确定性验证；真实同operation先拦截14项漂移、修复后正式准入，14项保留行未变 |
| P0-2 | P0 | 故障类型与最小恢复动作 -> verify: 输入/执行故障进入技术恢复；Agent判断合同不变的产品缺陷进入Task返工；明确义务变化才进入Plan；实际决策提交/应用测试，不靠关键词推断。 | 已实现并通过准入/受控动作/输入消费测试；真实生产行只读编译通过 |
| P0-3 | P0 | 已修证据接续真实闭环 -> verify: 关联0346保留Task证据任务；真实新候选不重开有效旧任务，旧证明精确绑定，Candidate/Judge/Goal/local delivery以正式结果签收。隔离通过不标真实完成。 | 真实16任务完成、新候选冻结、14份原回执接续已验证；候选/Judge/Goal/local delivery未完成，Owner已批准采证上限100→120且19/19宿主采证成功；Reader重试冻结身份已修复并部署，正式语义验收继续 |
| P1-1 | P1 | Autoresearch子退出事实 -> verify: 自定义state_dir、同Run绑定；预算、Owner wait、未知退出分别保留原事件ref和检查点；父流程不误判源码缺陷，不擅自恢复。 | 已实现并通过回归；历史真实子Run已准确回放为wall_clock预算停止 |
| P1-2 | P1 | 恢复纵向回归与活性 -> verify: 失败→RM/OA→修复→独立验证→受控应用→恢复→最终签收；未完成且无执行/队列/明确阻塞必须产出诊断请求，重放幂等，不重新实现已有机制。 | 确定性验证通过；补齐Candidate infra诊断Owner与发布崩溃幂等；真实最终签收归P0-3 |

## 验证与收尾

- 保留 `scripts/dev-verify.py plan --base dev` 的全分支规划，运行相对49d343e0已验基线的增量影响闭包，补精确跨边界测试；sentinel和flow mock必须通过。
- 使用SOURCE现有完整Python环境，避免REPAIR最小venv误调用系统pytest。真实Run保持失败账本，所有人为部署/恢复记入reports；每个接受主张单独签收。
- 修复后实施commit、记录hash；仅验收完成项归档。尚无真实证明的条款保留active和具体剩余条件。

## 已复现与修复

1. Planner保持方法原来只有提示约束；实际草稿改了14个已完成保留任务，直到Critic才拒绝。新预检在validate/submit/聚合共用，取绑定的旧Plan选择，不接受候选自扩范围豁免。
2. OA Candidate入口原来只允许return_to_plan；新增合同不变的受控successor。独立审查发现legacy gap转换不能接受生产行的source_ref，且会改变evidence_contract，已增加严格canonical行模式，真实MEDIAN/AVERAGE只读编译及输入消费测试通过。
3. 带anchor Task ID的Candidate infra事件原来被RM视为Task L1并跳过；现在产生diagnose-attention，仍不替Agent判产品缺陷。
4. Autoresearch父流程原来把子Run预算停止归成diagnosis_runner_failed；现在保留同Run停止/预算因果、WRC、Owner wait、退出码和下一责任人。未知保持未知。
5. 崩溃注入证实amended/ready/result写入后、OA applied之前退出会重复发布。新增按checkpoint/Run/类型的完整身份一致性复用；三处崩溃重放均只发布一次。畸形predecessor输入改为结构化拒绝。
6. 独立审查补充CR2：相互依赖的两个Task同时替换时，旧依赖ID与合法新ID被误判合同漂移。复用原图编译器的一对一依赖重绑作为比较预期，仍保留全部前置依赖并拒绝OA删除依赖。
7. 独立审查CR3发现原测试Writer缺少生产kernel origin，未触发ready导致的Candidate失效检查。改为生产身份后两个崩溃窗口复现；新增仅确认既有完整发布链的回执恢复，不重新准入旧Candidate。终态Run只补历史回执，外部ready/损坏侧车拒绝，未发布ready仍按currentness检查。

实现提交：`adf18198 fix: preserve scoped recovery contracts and diagnostic ownership`。P0-3尚未完成，因此整批保持active，不归档为done。

## 验证证据

- 全分支规划：`scripts/dev-verify.py plan --base dev`；相对49d343e0增量确定性闭包：6994 passed、8 skipped。初次两项旧字段断言失败已更新为复数permitted_recovery_actions后重跑通过。
- canonical行/直接调用闭包：565 passed；最后追加崩溃幂等修正的直接调用闭包：579 passed；checkpoint漂移/跨Run隔离新增测试1 passed；最终sentinel 53 passed，flow mock 10 passed。
- 纵向mock用真实Git、结果准入、账本和受控动作：post-verify discovery Reader故障→RM诊断→测试Owner批准输入修复→控制器对象重建→Reader→Judge→Goal完成。Provider/Owner为测试输入；不称为真实Provider、OS崩溃或无监工闭环。
- OA产品修复另测真实submit→受控Task Map→writer选择、前任输入解析，含生产行形状、aggregate未解决成员、终态Run、次数及越权负例；不与mock拼接宣称一条未经执行的端到端链。
- 临时日志和现场只读探针保留在`/tmp/zf-eight-qualification-20261120T122929Z/p0-*`及`p1-*`。真实恢复与最终交付仍待P0-3签收，原无监工资格失败不变。

CR2增量验证：module gap及Candidate调用闭包192 passed，最终正负例25 passed，sentinel 53 passed。

CR3增量验证：生产origin的崩溃与边界21 passed，OA/恢复直接调用闭包278 passed。旧的空origin测试结果不能作为生产崩溃恢复证明。

## 真实恢复记录

06:00 UTC已将`adf18198`及`824556e7`快进到隔离控制面。记录本轮用户授权，通过既有Owner控制动作恢复一次stage代次，触发`evt-b662f50bca09`；不修改预算、权限或历史失败。控制器PID958768，既有tmux会话保留。Planner于06:01继续运行，新operation为`wop-prd-plan-planner-trig-evt-046dea37-rework-57e95e5770d3`；采用批准Plan的draft-baseline。最终Candidate/Judge/Goal/local delivery仍待签收。

最终自动验证（base 824556e7，显式tests/test_candidate_task_repair_decision.py绑定新模块）：380 passed、7 skipped；sentinel 53 passed、flow mock 10 passed。独立只读复核确认CR1/CR2/CR3无剩余阻塞，复核本身不构成真实8×8通过。

## 真实恢复新增缺陷 CR4 / CR5

上面的06:01 Planner后来核实属于错误分支，不能算作正确局部恢复：03:50的旧
`task_map.ready`（evt-6b0f0b7e6cb7）在06:00重新消费，package admission失败产生
evt-7b0c97b15e15，再自动生成全范围Plan触发evt-046dea37ddd3，覆盖原授权的
evt-b662f50bca09。旧事件不是06:00新作出的RM决策。控制器06:22暂停；Critic在
06:25以rejected结束，证据留存，不批准或派发这个全范围Plan。

- CR4：Goal草稿兼容。真实16行旧式Map的Goal由AC派生，直接seed会造成71项校验错误，
  Planner改14项保留行的Goal引用。修复为条件seed/原字节保持/参考Goal侧车；不放宽preflight。
- CR5：旧writer回放覆盖新Owner恢复。在package admission之前核已完成受控Plan恢复；
  早于它的同Run/PDD writer意图抑制。已存在的原生因果Plan后代通过既有取消Owner退役，
  reader起点、聚合和synth结果均检查，避免消费旧拒绝再触发重规划。
- 定向回归13 passed。真实只读探针`p0-frontier-real-probe.json`确认旧writer及046分支
  都退役到b662，而b662本身不被拦；Goal草稿保持原始字节、16行完整。
- 计划沿同一stage代次用既有`stage-retrigger`恢复b662；不新开预算、不修改16个done历史事实。
  P0-3继续active，必须等真实Candidate/Judge/Goal/local delivery结果。

CR4/CR5验证：自动影响闭包（base643347e8）3529 passed、8 skipped、8 failed；8项均为
两个Plan synth测试替身缺少新增退役接口，补齐替身后相关文件与全部新回归28 passed。
新增实际ControlledActionService重试测试确认范围及stage代次原样保留。最终sentinel
53 passed，flow mock 10 passed；不称为全量测试或真实产品验收。

CR4/CR5实现提交：`55479d4a fix: retire stale writer recovery before plan redispatch`；此前
`824556e7`（依赖身份）与`643347e8`（生产回执恢复）也已部署。隔离控制面快进至55479d4a，
06:41 UTC受控`stage-retrigger`产生evt-59cfee3d270c，沿用stage-replan-69ddde1757e436c74465。
新控制器PID1105899；实际产生evt-a7ea1e0dd7b5取消046错误分支，原因准确为
plan_branch_descends_from_retired_writer_trigger；evt-422d5045c127派发新的局部Planner。
19个既有pane中18个PID保持，Planner按新派发替换；未重启其他项目控制器。

保留被退役Critic的两项语义意见作为重试反馈：八个只读审计的验收责任表需一致，
MEDIAN输入必须保留全部已批准验收义务，不能缩成单个回归例。取消旧分支不丢弃意见。
真实入口证据：reports/p0-frontier-live-entry-proof.json；受控动作：
reports/p0-frontier-controlled-retry.json。仍无新的Candidate/Judge/Goal/local delivery签收。

复核边界：此前独立复核关闭CR1–CR3；CR4/CR5的独立研究会话遇到Astra容量不足，
没有最终复核结果。本批有主会话源码审查、确定性回归与上述真实入口证据，不将其冒充独立通过。

06:47真实Planner调用draft-baseline成功，`p0-goal-seed-live-proof.json`记录原Map与本地草稿
SHA完全相等（249c46d8…）、16行和Goal参考侧车完整。06:52已读25/25项，进入计划修订。
独立研究会话随后从容量错误恢复，仍需等最终结果。

CR5补充回归用实际`stall_redispatch_event`复现：晚于Owner恢复的新副本仍承载旧意图，
仅按副本时间会放行。现复用精确stall身份解析，以原触发位置判定；payload/Run/Task改变
不归并。抑制事件补stage_id并结算原触发，使`emit_stall_recoveries`不继续派发/升级它。
原始失败日志`p0-late-stall-red.log`；此修正不改变正在执行的有效Planner输入。

晚到副本验证：writer/reader/fanout/synth/Event及恢复mock调用闭包439 passed；最后补producer
task_id回填后的定向61 passed；sentinel53 passed、flow mock10 passed。没有降低cap或删改事件。

晚到副本实现提交：`39ae69ca fix: keep late stall retries bound to retired writer intents`。
控制器更新到PID1172300；`reports/p0-late-stall-deploy.json`证明所有pane PID、Planner
operation/attempt均与重启前一致，没有新stage触发。

07:04真实Planner调用`result validate --scratch --summary`拒绝14项preserved_task_changed，
未派发Critic；证据`reports/p0-preservation-live-validate.txt`。Planner随后读取完整结构化
反馈并声明原地恢复保留行。当前正确的baseline导出已存在，但实际输出仍沿用了旧工作区
文件；补充skill要求使用本次返回的路径/明确复制基线并绑定result refs，避免导出成功后
继续编辑历史失败草稿。该方法修正不放宽预检，也不代Worker提交。P0-3仍等待正式签收。

skill补充自动验证41 passed、1 skipped；canonical与两份分发副本逐字节一致。
07:07同一真实Planner已恢复14项保留行，预检仅剩AC-MEDIAN的mandatory_claim_rewritten，
正原地恢复强制Goal原文。没有因此消耗新的Plan/Critic派发；待零错误正式提交后再签收。

## 真实局部计划准入与自动返工（07:11–07:29 UTC）

- Planner 在同一 operation 修复完所有预检错误后正式提交，完成事件
  `evt-ee7dbc03516c`、准入 `evt-6eae5d3789d1`；用时约29分32秒。
  冻结Map摘要`107a2f82b49008916742cac33a6ebe25ae87e7a31b2c13af71012a0a57693b6e`，
  仍有16项Task、19条命令，仅MEDIAN行变化，14项范围外保留行逐项相等。
  原Goal原文保留，新增AC-MEDIAN-SUBNORMAL。证据：
  `reports/p0-final-planner-control-result.json`、`reports/p0-admitted-plan-preservation.json`。
- Critic 运行约9分30秒后正式拒绝：前八个只读审计的Acceptance Matrix和execution_spec
  仍将验收/命令Owner写为task_verify，与已确认的candidate_verify不一致。
  逐项核实冻结输出后确认这是真实语义遗漏；Planner正文声称修正不构成字段已修。
  Critic保留8条精确fix_items，其他本轮范围/执行约束通过其评审。结果事件
  `evt-066e69bd0794`，证据`reports/p0-scoped-critic-rejection.json`。
- `prd.plan.failed`事件`evt-e77ae9ba3477`之后约22秒，运行时自动派发第二轮Planner
  （`evt-503516488724` → `evt-c254b6d602a7`），没有监工手动重启。
  仍为AVERAGE/MEDIAN、failed_children_only、原stage代次；当前2/2自动返工额度，
  不能任意重置或再授新代次。
- 新输入显示78项必读选择，实际为24个source/artifact组合；一份前轮typed结果拆成55个
  顶层字段选择。未发现78份文件或精确必读引用重复，不以计数增长直接判定加载实现失效。
  证据`reports/p0-current-replan-input-inventory.json`。
- P0-3仍active：以上证明预检生效及失败后自动接续，不证明Candidate、Judge、Goal或
  local delivery已通过；原无人监工窗口失败不变。

独立研究会话07:23已完成CR4/CR5限定源码复核（07:30读取报告确认），审查HEAD为
`7af14d77`，未发现确定的剩余阻塞。复核阅读真实证明文件和测试源码，没有独立运行测试
或操作Run；不等于产品验收。报告为实验目录中的`loopforge-recovery-review.md`及
`loopforge-recovery-review-status.json`，其`cr4_cr5_final_review`节记录最终范围及文件摘要。

## 基线补充端口导出缺口（07:34 UTC）

实际第二轮Planner调用draft-baseline时，execution_spec、task_execution_input及
product_acceptance_spec被CLI静态choices拒绝；它们已是当前Package分配的合法Planner
输出。原方法要求从已批准正文开始，但工具只支持部分端口，模型随后重写补充规格。
这是可复现工具缺口，不据此把所有模型遗漏归咎同一原因。真实CLI回归3 failed
（`p0-supplemental-draft-red.log`）。补齐三项名称，沿用全部原身份、全文ACL、摘要和目录
校验；不导出需求/审批/Goal权威供编辑，不豁免必读。完成验证后部署CLI，无须中断Worker。

补充端口验证：真实CLI/保留行定向80 passed；相对7af14d77的自动影响闭包175 passed、
1 skipped；sentinel53 passed、flow mock10 passed，三份skill一致。完整分支的`--base dev`
规划单独留存，本次未重复运行已验证的整个历史闭包。日志为`p0-supplemental-draft-*`。

补充端口实现`ac4b1e8b fix: export supplemental plan ports from the pinned baseline`，
07:36隔离源码快进，控制器PID1172300未变；CLI新调用直接加载。进一步核对实际briefing
发现仍只渲染旧三端口，真实render→CLI parser回归1 failed/7 passed；改为与CLI共用名单，
只导出本attempt分配且全文可读的可编辑端口，不把需求/评审、局部选择或不存在端口加入。

07:38核实宿主采证额度：上限100、已用90、剩10。修复后新target的19条规范命令不能直接
复用旧target收据。已询问Owner是否批准提高到120；未收到答复前保持配置原值。当前Plan
仍按原范围推进，未以未来额度不足假装当前任务已停止。

简报入口验证：初次定向命令误写一个不存在的测试路径，退出4且未执行测试，随后更正；
有效定向86 passed。相对ac4b1e8b自动影响闭包2699 passed、sentinel53 passed，另跑flow
mock10 passed。测试从实际render入口取命令交给CLI parser，核已分配补充端口被列入、
局部ACL及需求/评审权威不被列入；没有增加输入必读范围或免除权限检查。

07:44第二轮Planner再次完成同operation内预检修复，正式提交
`evt-6ec73838d11c`（准入`evt-3fda2aa8e10e`），耗时约23分18秒。
冻结输出仍16项Task、19条命令，仅MEDIAN行变化；八项Acceptance Matrix归属已修正，
执行规格改为重新生成正文，须由Critic核验完整性，不能按preflight通过提前签收。
07:45 Critic自动派发`wop-prd-plan-synth-trig-evt-50351648-97e1fe52f93d`。
证据`reports/p0-second-planner-control-result.json`、`reports/p0-second-plan-preservation.json`。

## Critic通过后的审批正文断链

`664e4ba4 fix: expose assigned baseline ports in planner briefings`已于07:48载入隔离控制器
PID1362582；所有pane PID及Critic operation/attempt/request_ref不变，未新建stage代次。
Critic 07:54正式通过（`evt-334f6ed5cf04`、准入`evt-4b4d0cb4465a`），约9分23秒；
`evt-bee26e4ebf31` ready与`evt-8053186c939f`审批请求确实只选AVERAGE/MEDIAN。

审批前dry-run发现Plan Preview正文不可读，故尚未批准。Critic预填的synth-plan.md不存在；
它这次输出source_id:digest证据定位符，旧恢复入口仅识别Markdown路径。实际已审核的
Planner正文及其摘要仍完整存在于当前typed candidate contract，未丢失文件。

新只读恢复沿ready的精确Critic因果、reviewed generation和合同/author/TaskMap/Plan
摘要找唯一正文；不存在或跨Run/代次、改Map、篡改正文/作者、歧义、局部授权均拒绝。
实际preview入口原2 failed/9 passed；初步调用回归72 passed。真实只读探针已恢复完整
正文SHA59281812079e84a63dbe47fce9ced42120f1f6792455537bbee55d4cfbbf1103，内容与冻结
Planner正文相等，content_error/artifacts_error为空。证据`p0-plan-preview-red.log`及
`reports/p0-plan-preview-repair-probe.json`；尚未因此批准或修改任何运行态。

独立复核PV1补充：原helper拒绝后仍可经旧无摘要Markdown fallback读出正文。
混合locator+可读路径的实际preview负例复现9 failed（`p0-plan-preview-fallback-red.log`）；
已阻止typed审核合同失败后降级，保留无typed合同的历史兼容。描述符allowed_paths只是
实际携带字段时的防御；合同source_bindings不是完整Worker ACL，不能声称本补丁验证
了其中不存在的权限元数据。这里只沿Operator Preview既有权限做只读展示。

预览补修最终验证：含页面/API路径的自动调用闭包249 passed，新增独立locator正例后的
定向13 passed，sentinel53 passed、flow mock10 passed。独立研究复核确认PV1已关闭，
仅读源码及主监工日志、未独立执行测试；不据此宣告真实产品完成。

`3da8db2a fix: recover plan previews from the exact reviewed author`于08:07加载，
控制器PID1433161，Worker pane PID保留。审批dry-run初次把历史archive一并计数得到48条，
该检查脚本已改为TaskStore.get逐ID核当前16项；这不是新增业务Task，也不是产品修复。
真实预览与准确版本审阅通过后，08:08:41执行既有委托的Plan批准`evt-5a92e418ccbd`。
没有新增stage代次或增加宿主预算。审阅文件`reports/p0-scoped-plan-owner-review.json`。

运行时随后产生`evt-f24051344e80`局部pipeline代次，仅派发MEDIAN与AVERAGE；
08:08:57 / 08:09:07两个真实Impl进入running（dev-lane-0/1），当前14 done、2 in_progress。
其余14项没有重新实施。P0-3仍需独立Verify、新Candidate、Judge/Goal/local delivery正式
签收，不能把成功派发当作真实闭环完成。Owner增加宿主采证额度的答复仍未收到。

## MEDIAN宿主发布恢复补修（08:26 UTC）

AVERAGE的Impl正式准入后08:22自动派发独立Verify；MEDIAN完成真实RED/GREEN，但Git
索引只读，08:26:24提交`host_publication_required`，canonical `evt-6458307a9a99`、
admission `evt-43b74a788d20`。运行时反复defer，原因`another operation owns the task`。
只读回放确认冲突者是旧Task Map已settled的generation3，而当前新Map为generation1。
不是仍有Worker占用，也不是当前业务测试失败；保留已有两文件改动与红绿证据。

修复宿主绑定只在相同Run/Map或不完整身份下比较代次数值；完整不同范围的终态历史
不再阻止当前发布，所有活跃占用继续拦截。另外复现类型化blocked使用dev.build.done，
后续retry仅接受旧式dev.blocked，导致提交后仍无法接续；按已准入语义结果和精确source
绑定兼容此transport事件。不扩大权限/预算，不直接手工提交产品变更。

新增回归初次3 failed、5 passed；首轮定向85 passed。后续影响闭包与真实恢复待记录。

补修影响闭包465 passed；新增实际run_manager_tick回归与Flow smoke mock合计17 passed，
sentinel53 passed。真实账本只读探针确认当前MEDIAN的binding及typed canonical source
已可通过，不执行Git或发事件。证据`p0-publication-real-binding-probe.json`；其他日志
为`p0-publication-*`。08:33 AVERAGE独立Verify通过并准入`evt-8783b3abbadb`，
8条定向测试通过，候选级条款留给后续阶段。

补修提交`8d385ad8 fix: resume task publication across plan generations`；独立研究源码
复核未发现确定剩余问题，未独立执行测试。08:34控制器加载该提交（PID1543587），全部
既有pane PID保留。08:35:05 Run Manager自动发布MEDIAN（receipt evt-913e70a3da26），
提交`2621353213b6ef9a64e5fd6146febacd69350771`；随后自动宿主测试并保存回执。
08:35:32 `evt-101798a9b802`请求复核，08:35:50运行时派发同Map的Impl generation2。
无手工产品Git提交、无新Plan/Critic，无预算或权限变更。宿主采证累计因此增加一次，
上限仍100；此前增加额度的Owner答复仍未收到。P0-3继续等待最终签收。

09:05:18 MEDIAN独立Verify正式通过（evt-b4c0ec3294e2），09:05:41自动整合，
09:05:44冻结新Candidate `bc0aebc8f53e91fb17a93dce1b0eef03aff76a03`
（evt-c664a40dd257），16项当前Task均done，随后自动启动8路候选Verify。
冻结包16份整合回执逐份hydrate/digest校验通过；与前候选evt-62cd367e1e56比较，
14份原回执ref/digest原样保留，只有AVERAGE/MEDIAN为本轮新回执。证据分别为
`p0-new-candidate-ready-proof.json`和`p0-new-candidate-retained-receipts.json`。
此证明局部恢复和证据接续已生效，尚不证明候选验收、Judge/Goal/local delivery完成。

耗时限制仍如实保留：MEDIAN复核Impl约13分34秒、独立Verify约15分37秒；
两者输入交付分别19/19及20/20，期间有游标/分页参数自行纠正、一次上下文压缩及
测试事件引用检索。未将这些额外耗时伪称完全解决，也不把Provider工具用法错误
直接归因为源码缺陷。当前P0/P1范围内实际阻塞修复与上述候选签收分别记录。

09:09只读授权预检：8路候选Verify全部running，同一目标bc0aebc8、同一合同
`d841bd7019dacc6c6f89de7e913a89fbea21bb81e009ceaa19d2212b3fae6e73`，均绑定
19条命令。宿主采证上限100、已用91、剩9；旧目标回执不能冒充新目标回执，完整闭环
仍需此前提出的100→120授权答复。证据`p0-candidate-authorization-preflight.json`。
此时尚未发生runtime budget exhaustion，八路仍执行；不标Run paused/blocked，不停止
已授权工作，不自改policy或approval_ref，也不将原无人监工失败改成通过。


## Owner 上调采证额度与 Reader 执行失败（2026-11-21）

Owner明确要求“提高的120”，授权本Run的host verification上限100→120，保持
approval_ref及累计用量。09:30:56事件evt-59a85bdf1f67已报额度耗尽；不是运行时间
上限，也不是新的产品缺陷。此时当前8路Reader均settled/abstained，准入结果均为
verifier_execution_failure，无法完成Docker浏览器路径；本Run没有Judge operation，
不把Reader执行失败称作Judge拒绝。研究已交给既有cdd-1 pane1原生session，仅读
LoopForge/loopx及事件证据，不替代Reader裁决或修改Run。

本次复现三处阻塞：配置解析硬限100；同manifest的历史budget escalation永久阻止
重判；policy_digest包含额度，导致上调后已有成功采证回执全部失配。新增RED四项
失败、九项通过，日志host-budget-continuation-red.log；初轮修复回归136 passed。
修复仅解除硬编码100天花板，保留显式整数额度；同Run/approval_ref计数不归零。
只有明确budget原因在当前配置下重判，未知effect及其他原因仍阻断；解除额度后若
出现新原因，单独记录该原因，旧budget告警不能隐藏它。

新回执保存完整policy_snapshot并校验原摘要，只有额度单调增加且其余字段完全相同
才保持有效；旧无snapshot回执只匹配旧parser实际可接受的1..100有限历史值。更旧
无采集大小字段的格式仍核验留存字节上限。命令、镜像、网络、保护文件、授权身份
或超时变化不会借额度兼容获得放行；不改写历史回执，不把采证当作语义通过。

补充实际Reader受控恢复入口在额度增加后消费旧回执、一次恢复及失败回执交回验证；
实际容器执行器测试校验新snapshot产出。P0-3仍待正式Candidate/Judge/Goal/local
交付，不把这次人工修复后的进展写成原无监工窗口通过。

额度接续最终验证：相对a1c471d5自动影响闭包8810 passed、10 skipped；sentinel53
passed、flow mock10 passed。追加定向与Candidate恢复mock86 passed。一次错误测试
路径未执行测试；一次误用系统解释器的启动已终止，均不计成功，最终receipt来自
SOURCE完整venv。全分支plan、最终自动验证receipt、日志保存为host-budget-*。
真实只读预检确认旧额度100/已用100，当前候选9份采证回执在120配置下均保持有效。
独立research源码复核未发现确定剩余阻塞，未独立执行测试；真实部署接续另行记录。


额度修复提交`7c423287 fix: continue host verification after an authorized budget increase`。
09:51:31 UTC加载到隔离控制器PID1882102，仅改配置100→120，Worker pane PID全部
保留、原授权身份及计数不变。09:52:02起原Run Manager自动继续采证；09:57:40当前
候选18/19份回执有效且退出码均0，累计109/120。未合并dev或push。

### 真实复验追加：静默闸门仍读旧额度告警

09:57:58 `evt-b67d24c40f21 run.goal.quiescent.entered`引用旧预算告警
`evt-4cf37bcef07d`。旧告警宽限窗10分钟届满后，tick_services整体提前返回，
最后一条unique_words命令没有派发；不是Docker失败，也没有Judge执行。前一补丁
只修executor内部判断，漏了上游静默入口，不能以初步恢复或隔离通过宣告真实闭环。

新增实际run_standard_tick_services复现先1 failed/8 passed；门禁补修只让来源为
kernel/Run Manager、明确预算耗尽、仍有同Run/approval_ref额度的等待重新进入RM。
同manifest其他原因或未结算effect仍静默，原告警不删除；这只是调度唤醒，执行器
继续核验当前Run、operation、目标、合同与所有权限。不会把旧告警转换为产品通过。

定向修正测试fixture补上生产中实际存在的已完成effect唤醒事实后117 passed；最初
自动闭包使用了旧fixture并失败，不能记为成功，最终闭包另记录。真实只读回放和
部署效果分别留证，继续保持P0-3 active。

静默补修最终自动调用闭包125 passed，sentinel53 passed、flow mock10 passed。
真实账本只读对照：已部署7c423287仍为escalate_unresolved（evt-4cf37bcef07d），
修复工作区为host_verification_budget_available；二者均读取同一120额度，没有
执行effect或发事件。证据host-budget-quiescence-deployed.json/repaired.json。


### 19/19 采证后的恢复身份故障

`077b5a3d fix: wake budget recovery through the quiescence gate`于10:07:34 UTC
加载，Worker pane全部保留。10:07:44静默退出；10:08:05最后unique_words采证成功，
当前冻结候选19/19成功，累计110/120，同approval_ref。自动受控恢复evt-4749b975e3d0
随后两轮Reader均在operation preregistration失败，没有启动新Reader，更没有Judge。
10:12:43仅停止控制器服务防止已知错误循环；不修改Run/Task状态和历史失败。

复现根因：精确fanout恢复复制时过滤两个空数组，导致原生freeze receipt全字段校验
拒绝；恢复副本还携带native schema，使新事件ID进入contract/target摘要，不能合法
消费19份原回执。workflow_resume另有独立冻结锚点解析；只改派发入口不够。

最小修复：精确fanout lineage保留完整字段；新Candidate Reader恢复只发引用原native
freeze的调度通知。历史坏副本通过完整kernel failure→fanout→native、批准请求、
受控action started/applied因果和时序解析回原身份，仅兼容两个本来为[]的缺键；
显式null、其他缺失、变更目标/receipt/approval、跨Run、缺applied、新native取代均拒绝。
Reader输入/结果currentness、成功消费和resume投影共用解析；缺因果产生明确不可
变更checkpoint，不令整个投影崩溃。显式父ID失效时不回退到同freeze_id搜索。

Reader preregistration失败按kernel已知的启动前阶段记录infra/reader_execution_failure，
不再默认质量返工。原有两个错误质量路由事件保留；真实只读检查16Tasks仍done、
14份retained proofs完整，原contract摘要d841bd…不变，currentness无错误。
证明retained-freeze-repaired-identity-proof.json；研究报告reader-judge-loopforge-loopx-research.md
保留LoopForge/LoopX借鉴和未验证边界，不借宿主命令成功替代独立语义验收。

初始身份回归RED4 failed/7 passed，调用闭包首轮105 passed。新增实际batch投影测试
发现调用方传入裁剪后的failure_inputs会丢授权因果事件，改用完整账本后再验证。
测试fixture参数错误/无效文件路径不计验证成功，最终验证结果另记。

最终身份修复验证：新增23项通过；相对077b5a3d的自动影响闭包6030 passed、7 skipped，
sentinel53 passed、flow smoke10 passed。完整命令输出retained-freeze-automatic.log；
全分支plan与增量plan均已保存。独立research复核RR1在真实batch投影已关闭，未发现
确定剩余阻塞；其未独立重跑pytest。仍需部署后确认8路Reader消费及正式Judge结果。

身份接续实现提交`2af47d09 fix: preserve native candidate identity across reader recovery`。
10:34:53 UTC隔离控制器PID2069908运行。配置及计数不变；部署脚本“全部pane保留”
断言失败，未掩盖：启动生命周期正式suspend了8个没有活跃operation的Reader role，
其余pane PID不变。保留原失败报告并追加retained-freeze-pane-reconciliation.json，
不能宣称所有pane都保留。10:37:32通过既有stage-retrigger受控动作发evt-c538793be1e9，
恢复未完成Reader；10:38:11首个新operation注册成功，19份回执仍有效、用量110/120。
未合并dev或push；原无监工失败不变，P0-3继续active等待独立验收与交付。

10:41:20 UTC现场确认8/8 Reader operation均running，已开始产生独立artifact阅读记录；
每路冻结请求都合法绑定19份原始回执，contract/target currentness无错误，source
operation保持原值，恢复以来无重复host effect。证明retained-freeze-eight-reader-binding-proof.json。
Worker仍为GPT-6-Luna high；Judge尚未派发，既有6.1 Sol high原生切换watcher存活。
本轮注册失败已解除，但Reader语义结论、Judge、Goal/local delivery尚未签收。
