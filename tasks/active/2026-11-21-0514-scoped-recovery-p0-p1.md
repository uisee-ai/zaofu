# 8×8 局部恢复收敛与诊断闭环 P0–P1

> 状态: active

Owner 已明确授权“好，执行p0-p1修复”。基于隔离分支49d343e0实施已讨论的五项；先复现、最小修复、影响闭包、提交，随后在既有授权范围内受控真实复测。原无监工窗口失败保持；不合ZaoFu dev、不push、不修改canonical JSON、不代Worker提交结果。

| ID | 优先级 | 工作与验收 | 状态 |
|---|---|---|---|
| P0-1 | P0 | 局部Planner返工基线及范围约束 -> verify: 实际提交预检在Critic前拒绝范围外合同/命令/责任漂移，合法局部变化通过；范围来自Agent已准入决定，Runtime只核绑定/差量。 | 已实现并通过确定性验证；真实同operation先拦截14项漂移、修复后正式准入，14项保留行未变 |
| P0-2 | P0 | 故障类型与最小恢复动作 -> verify: 输入/执行故障进入技术恢复；Agent判断合同不变的产品缺陷进入Task返工；明确义务变化才进入Plan；实际决策提交/应用测试，不靠关键词推断。 | 已实现并通过准入/受控动作/输入消费测试；真实生产行只读编译通过 |
| P0-3 | P0 | 已修证据接续真实闭环 -> verify: 关联0346保留Task证据任务；真实新候选不重开有效旧任务，旧证明精确绑定，Candidate/Judge/Goal/local delivery以正式结果签收。隔离通过不标真实完成。 | 待真实验证 |
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
