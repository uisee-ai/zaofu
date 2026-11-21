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

实现提交：`adf18198 fix: preserve scoped recovery contracts and diagnostic ownership`。P0-3尚未完成，因此整批保持active，不归档为done。

## 验证证据

- 全分支规划：`scripts/dev-verify.py plan --base dev`；相对49d343e0增量确定性闭包：6994 passed、8 skipped。初次两项旧字段断言失败已更新为复数permitted_recovery_actions后重跑通过。
- canonical行/直接调用闭包：565 passed；最后追加崩溃幂等修正的直接调用闭包：579 passed；checkpoint漂移/跨Run隔离新增测试1 passed；最终sentinel 53 passed，flow mock 10 passed。
- 纵向mock用真实Git、结果准入、账本和受控动作：post-verify discovery Reader故障→RM诊断→测试Owner批准输入修复→控制器对象重建→Reader→Judge→Goal完成。Provider/Owner为测试输入；不称为真实Provider、OS崩溃或无监工闭环。
- OA产品修复另测真实submit→受控Task Map→writer选择、前任输入解析，含生产行形状、aggregate未解决成员、终态Run、次数及越权负例；不与mock拼接宣称一条未经执行的端到端链。
- 临时日志和现场只读探针保留在`/tmp/zf-eight-qualification-20261120T122929Z/p0-*`及`p1-*`。真实恢复与最终交付仍待P0-3签收，原无监工资格失败不变。

CR2增量验证：module gap及Candidate调用闭包192 passed，最终正负例25 passed，sentinel 53 passed。
