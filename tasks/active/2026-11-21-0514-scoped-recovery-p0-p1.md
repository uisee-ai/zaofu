# 8×8 局部恢复收敛与诊断闭环 P0–P1

> 状态: active

Owner 已明确授权“好，执行p0-p1修复”。基于隔离分支49d343e0实施已讨论的五项；先复现、最小修复、影响闭包、提交，随后在既有授权范围内受控真实复测。原无监工窗口失败保持；不合ZaoFu dev、不push、不修改canonical JSON、不代Worker提交结果。

| ID | 优先级 | 工作与验收 | 状态 |
|---|---|---|---|
| P0-1 | P0 | 局部Planner返工基线及范围约束 -> verify: 实际提交预检在Critic前拒绝范围外合同/命令/责任漂移，合法局部变化通过；范围来自Agent已准入决定，Runtime只核绑定/差量。 | 已实现并通过确定性验证；真实被拒草稿只读回放拦截14项漂移 |
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
