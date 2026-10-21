# 运行时结果身份与恢复效果闭环补丁

> 状态：P0/P1 implemented；现场恢复验收 active；2026-10-21。本设计由操作者明确批准实施，完成状态以关联任务的验证与提交为准。

## 问题与证据

洛书 `loshu-impl8-20261020` 的七个本地任务发生 29 次结果拒收。RTK 原始事件
`evt-3c4c0c4505cb` 返回 authority `r7f8…`，与 dispatch、合同快照、TaskStore 一致；
fanout child 的排队 payload 仍为 `r676…`，结果绑定将正确身份及自检身份覆盖成旧值。
legacy implementation adapter 又将 `impl_self_check` 丢成空 `self_check`。
这两项均在基线 `8406826` 上通过真实输入的只读函数回放复现。

恢复链将带 `task_contract_unsatisfiable` 的运行时故障归类为 design_issue，进入
discovery/replan。恢复动作仅以请求事件出现作为成功；随后 reader 失败耗尽 stage cap，
`evt-3f8927ad7587` 将 Run 封为 blocked。Autoresearch 的隔离配置因 86 项技能多来源
冲突而 validate_failed，候选被 dismissed，未形成源码修复。

## 权威与行为契约

1. TaskStore 管理当前合同；不可变 operation request 和 dispatch snapshot 绑定该次执行。
   排队 payload 不能覆盖派发身份。补充遗漏身份只能来自匹配 operation/request hash 的
   dispatch；显式冲突、真实过期返回仍须被拒收。retry/restart 必须保持这一规则。
2. legacy 适配必须保留实现证据及身份。`impl_self_check` 与 `self_check` 是输入兼容名，
   输出仍使用现有唯一 result schema；冲突报告错误，不通过改写证据来“修复”身份。
3. 内核结构化准入证据优先于 worker 对故障类别的自述。真过期结果按过期处理；
   harness 接收故障交由 Run Manager/诊断处理；产品范围判断交给 agent/skill，
   不能把任何 stale 错误一概认作 harness bug。
4. action accepted/applied 是交付事实，effect passed 才是恢复效果。
   重启/重新规划请求不能直接关闭 recovery case。效果绑定 case、run、task、generation，
   无关成功不能清除故障计数；失败和超时进入既有有界恢复策略。
5. Autoresearch 使用固定源码来源和隔离状态。相同物理技能来源去重，真实不同版本仍报错。
   环境失败表示诊断未完成，保留候选与可审计失败原因，不冒充已证伪。
6. 历史终态事件保持不可变；终态后的诊断/维修在独立操作中执行。现有
   `stage-replan-new-generation` 可在显式授权后追加同 Run 新阶段代次和 active 事件；
   不直接设置状态或绕过 cap。保留已有提交、未提交工作和历史事件，不编辑冻结 Plan。
7. Orchestrator agent 给出语义建议；Kernel 维持机械调度。shadow 建议必须明确未应用，
   不把 agent enabled 当作故障已接管。产品接入事实不由 runtime 补造。

## 实施切片与验收

| 批次 | ID | 交付及验收 |
| --- | --- | --- |
| 第一批 | P0-1 | 派发/结果身份一致；排队旧身份、重试、真过期结果正反例及真实 caller 回归 |
| 第一批 | P0-2 | legacy 结果无损适配；自检、身份、引用保留及冲突拒收 |
| 第一批 | P0-3 | 准入故障分流；有结构化 runtime 错误时不误走产品 replan，产品缺口仍可重规划 |
| 第二批 | P1-1 | 请求交付与效果分离；只有匹配效果关闭 case，无关成功/失败/重复回放受测试 |
| 第二批 | P1-2 | 诊断 worktree 技能来源和版本一致；实际配置 validate 通过并携带故障输入 |
| 第二批 | P1-3 | 诊断启动失败保留未确认候选；归档原因、允许有界重诊断，不生成无证据补丁 |
| 第二批 | P1-4 | 终态维修与受控续接；保留旧终态，授权新阶段代次，复用经重新验证的产物，状态一致 |

先写失败测试，记录 RED 原因，再实现 GREEN；mock E2E 覆盖实际 runtime caller。
使用 dev-verify 的影响闭包、sentinel gate 与相关确定性 E2E；真实 provider/浏览器验证
单独记录，不将 mock 并发计为真实产品八路并发。

## 现场恢复与 Q01–Q03

统一 watcher、Run Manager、Autoresearch、Dashboard 的运行源码及版本。盘点 task refs、
worker commits、dirty worktrees 后建立续接证据；仅复用同合同且重新验证的结果。
先用一个结果验证交接，再恢复 ready frontier；Verify 依赖候选集成，不能提前伪造 candidate。

Q01 正式工程/复用边界，Q02 真实 API/字段/联调，Q03 身份/会话/授权/撤权传播，
分别登记来源、版本、责任人和实际验证。现有文件只有本地前端授权，外部事实尚未齐备；
用户已要求全部完成，因此主动查证并索取缺失事实，不能通过 mock 或虚构负责人关闭。

## 关联任务

- [第一批 P0](../../tasks/done/2026-10-21-0316-result-authority-p0.md)
- [第二批 P1](../../tasks/done/2026-10-21-0316-recovery-closure-p1.md)
- [现场恢复与接入验收](../../tasks/active/2026-10-21-0316-loshu-resume-q01-q03.md)

历史架构 142 文档未包含于当前公开 checkout；以 AGENTS.md、现有 store/event/service
边界及回归测试为准，不补写或假称读取缺失文档。

## 补丁验收与限制

最终源码影响闭包 530 passed，serial 10 passed，sentinel 51 passed，flow-smoke 10 passed；
实际诊断配置 validate 通过。完整确定性套件发现 20 项未修改基线已有失败，已在
独立基线复现，具体分类与日志见关联 P0/P1 任务；未将聚焦验证称为全套全绿。

恢复请求交付后保持 verifying；匹配 worker 请求因果链或对应任务完成事实才结算。
新效果等待具有 1800 秒默认期限，可由动作 effect_timeout_seconds 指定，历史未声明
期限的 pending 不追溯超时。普通 task_map.ready 不可代替待验收的恢复效果。
诊断失败保留 unverified；重复消费幂等，修好环境后可发起新的有界诊断请求。

## 现场续接补充：Reader 因果身份

首次恢复已让 VEHICLE-MODELS、SCENES、ADMINISTRATORS、ATTACHMENTS 四个已有实现结果通过准入，证明 P0 修复进入真实调用路径。
随后发现 `plan_reader_stage_replan` 将失败 API-RULES 的目标与同 Run 最新 ATTACHMENTS 请求的任务、candidate 混合。恢复与重试 briefing 改为锚定失败 fanout 的原始 trigger；存在 fanout 身份但原始输入缺失时返回 reader_origin_missing，禁止借用其他任务上下文。
Run Manager 旧进展集合遗漏带准入引用的 fanout.child.completed，导致已完成任务被旧失败再次 replan；现在以 task_id 和完成状态/准入凭据抑制旧请求，失败及无凭据事件不生效。

[补充任务](../../tasks/done/2026-10-21-0354-reader-recovery-lineage.md)先复现两个 RED，修复后含真实 retry caller、Run Manager、受控恢复和两个 mock E2E 的 299 项测试通过。只读现场回放将 evt-e652eab0bdcf 正确绑定 LS8-LOCAL-API-RULES / 4f9959e，未写运行状态。现场操作和外部接入限制见[检查点](../records/2026-10-21-loshu-recovery-checkpoint.md)。


## 现场诊断环境补充：忽略的 Flow 来源

真实 Autoresearch 进入 runner 后发现源码 checkout 的 docs/ 忽略规则阻止新生成 PRD 的 git add。runner 对自身明确生成的 source_ref 使用强制暂存，仍以唯一 pathspec 提交，不扩大暂存范围。回归同时证明无关暂存内容不进入提交。
见[补充任务](../../tasks/done/2026-10-21-0412-autoresearch-ignored-flow-input.md)。这修复诊断启动条件，诊断 verdict 仍须以真实结果为准。


现场验证另显示：项目自身 gitignored 的候选验收输入可能跨 reader checkout 留存。恢复时须依据本次 operation/HEAD/source manifest 重建并留存旧输入引用；错误输入引发的 wrong HEAD 属于验收环境故障，不是产品失败。当前现场通过操作员绑定并让独立 reader 重跑，未把本项目路径硬编码进通用 runtime，也未将此人工步骤宣称为全自动能力。


## 现场 P0 补充：Verify 准入不能依赖传输路径

真实浏览器通过之后，reader 的非浏览器 verification-result 仍携带模板 evidence_refs 和 target_commit，旧 emit 路径却准入（evt-37adee35a439）。该准入不是本轮验收证据。
根因是 typed_result_admission 以 semantic_submit 标记整段早退；结构适配只执行 strict=False。现在带 contract snapshot ref/digest 的 verification-result 无论走 emit 还是受控 result-submit，都进入现有合同、目标快照、AC、canonical command、target、exit_code 与证据检查；无合同的历史兼容输入保留原边界。
严格 Verify 检查另拒绝 evidence_refs 中保留的尖括号模板槽位。它是引用格式检查，不把非空字符串解释为已经证实的产品质量，也不替代独立运行命令。
[补充任务](../../tasks/done/2026-10-21-0422-verification-evidence-admission.md)记录 RED/GREEN、真实 service caller 和新一代独立验证；旧事件不删除，不以旧准入冒充本次通过。


## 最新恢复边界

新代独立 reader 的环境失败报告已通过真实结果准入（evt-1d05765bfd15 / abstained），证明传输兼容与严格证据门同时生效。产品 reporter 只消费 task Verify/author context，candidate reader 的上下文不能替代该角色身份；三条非浏览器命令在断言前退出。浏览器本代通过只证明本地候选的相应旅程。
后续应由任务验证 owner 供应真实合同上下文，或由脚手架 owner 明确增加 candidate 复核协议，并保留角色、目标、operation、manifest 校验；禁止伪造 verify-lane/作者身份使测试变绿。上下文修复与 Q01–Q03 外部事实闭环仍由现场恢复任务跟踪，未计入已完成。


## 现场 P1 补充：Reader 不能经 batch resume 回到 Writer

最后一轮有效 abstained 后，旧 workflow_resume 看到 task_map/candidate refs 就认为 reader 不再属于 stage 恢复，错误生成 repair_failed_children，将 1 个 reader 环境故障扩展为 13 个业务任务。`workflow-batch-resume` 也未声明 effect contract，收到 task_map.ready/workflow.resume.applied 就关闭恢复 case。首次 P1 覆盖 semantic-replan/worker restart/candidate retrigger，遗漏这个独立入口；该遗漏已通过真实事件复现。

修复依据 fanout.started 的 topology 和实际 trigger 类型确定 ownership：flow.discovery.requested 的 reader 仍由 reader stage 的有界恢复负责，输入中的 task map/candidate refs 不能转移写权限。candidate.ready 触发的既有候选重验与 writer 业务返工保持原有入口。

对 repair_failed_children/trigger_rework 的 batch resume，动作收据后保持 effect pending；只接受本次 ControlledActionService 实际发出的唯一 task_map.ready 所触发的新 fanout 或 generation admission。同 Run 的无关事件、Plan 等待批准不结算；Plan 拒绝/准入失败或超时按失败处理。该效果仅表示恢复进入派发，绝不代表业务修复已验收。

[补充任务](../../tasks/done/2026-10-21-0643-reader-batch-recovery-effects.md)记录 RED、实际 controlled caller、case 状态和现场回放。

## P1 补充：已提交修复的 reader target 交接

现场 API-RULES reader 的三个 Vitest 在断言前被产品 reporter 拒绝：它只能读取 task Verify/author 输入。方法与协议应在产品脚手架解决，Kernel 不认识洛书文件名、角色槽或业务验收方法。产品独立 worktree 添加显式 candidate 输入绑定，校验 source manifest/operation/attempt/HEAD，保留真实 flow-discovery 身份，并将 candidate 收据与 task/author 收据隔离。Task Verify 责任不因本地 reader 补跑而自动关闭。

受控 `stage-replan-new-generation` 新增可选 `repair_target={source_commit,commit,ref}`，仍要求原 cap escalation 与 owner approval。仅支持 contract-bound `flow.discovery.requested`：旧 target 必须匹配，提交必须改变且为旧提交后代，显式本地 ref 必须解析到新 commit。通过既有 snapshot writer 生成新的不可变 target；保留旧合同、计划、AC、历史 target，不移动任何候选/main ref。不满足条件不得重开 Run；同一升级的幂等重放不能偷换 target。

验证责任：产品先 RED/GREEN、实际 reporter 命令与 Docker browser；Kernel 以真实 ControlledActionService 调用覆盖新 target、保留旧 snapshot、错误 ref/commit/来源/祖先/损坏 snapshot 及幂等冲突，再运行恢复影响闭包和 mock E2E。新动作成功只代表重新派发获准，不代表修复生效或 Q01–Q03 完成。

真实恢复额外暴露两处交接问题：compact legacy trigger 尚未生成 target snapshot，只有精确 candidate commit；该形态从候选 pin 派生新 snapshot。reader 的 workdir pin 原先又无条件使用旧 stage target_ref 覆盖新的 snapshot。现在有不可变 target 时先核验其摘要和身份，再以其 commit checkout；坏 snapshot 拒绝派发，不回退旧 ref。无 snapshot 的文档/普通 reader 保留原路径。实际 `Orchestrator.run_once` + 临时 Git worktree 的 RED/GREEN 覆盖 checkout 与派发事件，避免仅 action-level 测试遗漏此边界。

结果提交方法补充：[Verify 技能](../../skills/zf-yoke-test-evaluator-role-context/SKILL.md) 明确 content-addressed completion-payloads 是只读模板。legacy emit 的 worker 在自己的 workdir 完成 JSON，只替换正式 CLI 的 `--payload-file` 参数；profiled semantic submit 仍只更新当前授权 scratch。首次 reader briefing 同步说明这一输入/输出边界。transport retry 复用同 target 的原始收据时保留旧身份/时间，使用当前 envelope 提交并检查命令退出状态，不因报告已写完而省略提交。不放宽 scope guard，不让操作员代发 worker 结果。
