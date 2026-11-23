# 212 · Workflow artifact 读取与 Agent session 恢复规格

> 状态：candidate，供评审。适用范围为 ZaoFu 管理的 workflow worker。首个验证对象为本地 Codex Candidate Verify。本文以 [198 的输入与恢复合同](198-context-index-write-read-resume.md) 为基础，补充工作区交付方式、读取证据和恢复入口。

## 1. 用户可观察的结果

Agent 第一次接到任务时，从 briefing 得到当前步骤、关键目标和一个可打开的输入索引。已知来源可以直接按相对路径读，文件较大时可按区间读。中断后，Runtime 确定应继续的 operation，准备好该 worker 的恢复说明，再唤醒会话。Agent 能从原步骤继续，对遗忘的条款、变更的输入或缺失的证据定向补读。

本规格有两个独立交付目标。第一，**在同一份必读合同下减少定位、分页和恢复的交互成本**。第二，**经过需求与验收对账，减少合同中模型无须反复阅读的内容**。两个目标分开评测，否则输入变少可能被误报为文件工具更快。

## 2. 实跑依据与适用边界

2026-11-23，8×8 Run `workflow-ddd320aa0dc7c2ef` 的 Candidate `88992ce7` 产生 `candidate.ready` 事件 `evt-00274691df5f`。随后 lane 0 与 lane 1 的实际 Verify 派发事件分别为 `evt-1d87fa2a5693`、`evt-eed87099da70`。以下数据来自验证工作树加载的 Runtime `a5077a3e`。仓库相对链接以 dev `e178f43c` 为基线。实跑版本已有冻结交付策略的严格准入，实施批次 1 要先核对该入口在目标分支的落点。

| 现场事实 | 对规格的约束 |
| --- | --- |
| 每条 lane 有 40 项必读，在本 lane 内按选中正文去重为 39 份、473,542 字节。另将两份冻结请求逐行对照，40 行中有 39 行相同，`candidate-host-execution` 的摘要因消费绑定各异。 | 按单 lane 计去重收益。跨 lane 复用宿主执行事实时，每个 Reader 单独绑定，独立累计交付。 |
| 两条 lane 分别调用 98、184 次 `artifact read`，含定向读取、补读及失败调用。 | 分别统计工具调用、宿主交付耗时、从实际派发到结果准入的墙钟耗时。调用数不能直接推算耗时或费用。 |
| 已知 JSON 来源的 `artifact index --query candidate-host-execution` 在含二进制附件的清单上报 UTF-8 错误，事件 `evt-95cbc8e735bd`。现有记录没有指明具体哪一个附件抛错。 | 批次 1 回归原查询及文本正文检索。精确 ID 查询另测，媒体通过授权描述符定位。 |
| 真实会话中原生文件读取曾完整返回 checklist。另一次较大输出确实被截断。最后 lane 1 靠定向补读获得 40/40 项交付覆盖，正式判词仍为 blocked。 | 物化、工具返回、模型当前记忆、产品判词分别记录。接受能证明内容的不同读取入口。 |
| 另一轮 Reader 在整包尚未读完时遇到压缩，恢复从合同前部重读。设计 198 的独立小样本里，任务切片保留共享约束，5/5 样例评分相同，输入由 71,353 降为 15,915 tokens。 | 恢复要区分历史交付与当前上下文。切片评测支持验证方向；本轮耗时收益须另测。[198 对照报告](../records/2026-11-02-context-198-provider-evaluation.md) |
| 19 条宿主命令均已有退出码 0 的回执。18 项验收证据可用，浏览器命令也已执行（`evt-cacecbb4aaf4`），其声明路径与媒体采集索引不匹配，消费结果为 `unavailable`。 | 断点恢复可沿原执行事实定位证据缺口。恢复本身没有重跑浏览器命令的权限。 |

实现参考来自两个固定版本。LoopForge 将普通上游 artifact 物化到工作区，以相对路径、摘要和大小生成清单；可续用 session 时，发送短续行提示，确认会话丢失后重新构建输入。LoopX 用 Turn journal 核对 Goal、Agent 和已完成阶段，另行验证原生 session 绑定；已提交结果与执行结果未知有不同恢复路径。ZaoFu 采用前者的文件交付方式和后者的分层恢复思路。[LoopForge 工件交付](https://github.com/Tencent/LoopForge/blob/d58a85ad03635e832146916cec374b5673b76987/internal/engine/artifacts.go#L25) · [LoopForge 会话续行](https://github.com/Tencent/LoopForge/blob/d58a85ad03635e832146916cec374b5673b76987/internal/engine/node_execute.go#L729) · [LoopX Turn 恢复](https://github.com/huangruiteng/loopx/blob/00ef1c79ae5dfed0dfc4681968ee12369b510345/loopx/control_plane/turn_driver/turn_journal.ts#L370) · [LoopX session 绑定](https://github.com/huangruiteng/loopx/blob/00ef1c79ae5dfed0dfc4681968ee12369b510345/loopx/control_plane/turn_driver/codex_cli.py#L915)

## 3. 一次派发给 Agent 什么

下图中的文件名用于说明 worker 接口。实际路径由 Runtime 注入，存储根从 `zf.yaml` 的 `project.state_dir` 解析。

```text
TaskAttempt / operation / 事件 / 已批准输入与冻结快照
          |
          +-- Kernel 核对身份、权限、target、manifest，发布本次输入视图
          |      +-- INPUTS.md     来源名、用途、必读位置、可选材料、相对路径
          |      +-- inputs/...    原文或获准的精确切片，媒体保留原格式
          |      +-- RESUME.md     恢复时的步骤、已完成动作、未决项、证据入口
          |
          +-- briefing 给出入口 --> Agent 在 workdir 直接打开对应文件
                                    |
                                    +-- 普通探索：搜索、查看、按需展开
                                    +-- 严格必读：核验实际返回区间 --> 现有读取账本
                                    +-- 结果提交 --> 现有准入及角色评判
```

**冻结正文。** Kernel 以本次 manifest 绑定的 source occurrence 和获准 selector 构建视图。索引列明来源标识、来源版本摘要、selector、选中正文摘要、UTF-8 字节数、字符半开区间 `[start, end)`、内容类型和必读理由。文本视图逐字交付现有编码器产生的选中正文（selected text），保留换行及空字符串。只获准一个 JSON 子路径时仅发布该值。相同字节可复用存储，交付覆盖只在各来源分别获准且冻结合同声明别名时共享。可选来源未物化时给出受控取回入口。[正文快照与回读](../../src/zf/runtime/artifact_read_snapshot.py)

**访问期限。** 每个 attempt 的只读目录位于产品 Git checkout 之外。宿主私下生成视图，核验相对路径、名称碰撞和越界链接后原子发布。视图的访问期限绑定当前 operation、actor、attempt、manifest 及凭据代次和有效期。轮换、过期、撤销、封存或回收 lane 时，先隔离旧执行环境（含已经打开的文件），再向合法消费者开放新视图。按调用发生时的授权核验迟到返回，已失效的访问不能增加覆盖，旧消费者的合法见证只归旧绑定。无法执行同等访问期限的环境使用已授权 CLI。

例如 cwd 为任务的 `project/` 时，briefing 可直接给出宿主已映射的 `../inputs/<attempt>/RESUME.md`。该文件里的链接均相对于本次输入视图。若 Provider 沙箱只接受 cwd 内路径，宿主可在沙箱命名空间提供等价只读挂载。两种呈现都须在真正的 worker 沙箱内验证读权限、lane 隔离、Git 状态与清理行为。切换或重建时保留已有源码改动和 worker 草稿。

**严格必读的准入。** 实跑的 `observed` 策略在 CLI 路径核对 `stdout_flushed`、绑定的 Codex 会话公开返回、完整正文或分页及持久见证。dev 的输出观察器单独只能匹配返回内容。工作区读取要接入同一个冻结策略的覆盖判定，分三步验收。

1. 首试本地 `codex-cli 0.171.0` 的 `exec_command` 单文件直读。试验固定工具版本并检查公开调用参数和同一 call ID 的返回格式。宿主须能证明本次实际读取的是授权视图内指定的路径和范围，并绑定 run、operation、dispatch、attempt、actor、session、source occurrence 及 selector。只从命令字符串推测路径，或在另一调用看到相同正文，都不能证明此次读取。
2. 只对该调用实际返回且能无损还原的选中正文累计覆盖。包装、行号与多字节字符须能映射回字符区间。只补可定位的缺段，无法确认边界的部分保持缺失。空选中值同样需要绑定到该调用。适配器保存调用与公开返回的不可变见证及摘要，按原生工具的证据类型核验，`stdout_flushed` 专用于 CLI。已返回但未持久见证的内容待核验。
3. `artifact recover`、结果预检、正式准入和封存后重放对同一绑定使用同一覆盖判定，支持原生读一部分、CLI 补齐。重试读取原请求冻结的 `produced` 或 `observed` 策略，旧请求未声明时沿用既有 `produced` 默认。普通探索、搜索和媒体索引可直接打开。工具或 briefing 直投缺少合格见证时，给出同合同的 CLI 取回入口。仍无法核验时，列出确切缺口和诊断责任人。媒体内容、验收条款及产品判词由 Verify/Judge 按对应证据评判。[已有输出观察](../../src/zf/runtime/artifact_output_delivery.py)

## 4. 角色与材料选择

| 角色或阶段 | 默认向 Agent 交付 | 可按需展开或由 Runtime 消费 |
| --- | --- | --- |
| Scan、Planner、Replan、Plan Critic | 工作流目标、原始需求及当前批准的必读清单；返修附已有候选、变更位置和未闭环意见 | 按来源和条款定位其他授权材料；复用 198/208 的首次与增量评审规则。 |
| Impl / Rework | 本任务合同、适用共享约束、依赖接口、返工反馈，以及产品 workdir | 代码、测试和 skills 直接读。任务文档指向同一冻结来源，减少为找同一条款重复打开文档。 |
| Task Verify · `task-verify` | 本任务目标、验收义务、实现回执和适用交接 | 独立检查冻结目标的代码和测试，按证据 ID 打开历史执行记录。 |
| Task Pipeline 的 `acceptance_review` · `integration-acceptance-review` | 本任务合同、target、独立 Verify 结论及其余集成风险 | 按任务集成义务展开交接和原文，绑定本次评审的消费记录。 |
| Candidate Verify · `candidate-verify` | 候选任务集合、整体覆盖矩阵和跨任务约束 | 从每项验收义务定位跨任务证据、异常及媒体。 |
| Thin Judge · `thin-judge-goal-closure` | 当前 Goal/Claim、关联覆盖和闭环证据 | 围绕当前 Goal 展开未决原文。 |
| Discovery 或未注册的输入范围 | 以本次 manifest 与已批准必读清单为准 | Runtime 以派发的 `output_profile_id` 标识范围，同名角色分别绑定输入合同和消费记录。[角色输入定义](../../src/zf/runtime/artifact_role_inputs.py) |
| OA、Run Manager、Kernel、Ship | OA 检查点可用同一输入视图；RM 通过状态和案卷定位本次问题 | Kernel/Ship 继续按事件、Store 和绑定回执推进。读文件不触发批准、重新派发、宿主命令或发布。 |

**缩减必读范围走单独一轮。** 以宿主证据包为例，86 项 `input_refs` 中用于校验来源的字段可由 Runtime 全量核验；哪些结果、异常、附件和共享约束必须进入评审者上下文，由计划/验收责任人对照每项义务决定。保留义务 → 来源 → selector → 可展开原文的映射，明确遗漏风险与批准版本。未知依赖或缺乏覆盖依据时沿用原清单。任何生效的变更只用于按既有流程批准并冻结的新输入合同，不由正在执行的消费者删减条目。

## 5. 断点与 session 恢复合同

一次中断涉及三类事实。**Workflow checkpoint** 决定哪一步仍需执行，取自事件与 canonical Store。**工作记录和工作区 checkpoint** 提供草稿、改动、已执行动作及可核验回执，Agent 陈述保留其来源。**Provider session** 承载对话，能否复用取决于 Provider、角色、session 身份和当前操作绑定。Runtime 先确定前两项和执行权，再为本次唤醒选择会话。

`RESUME.md` 的首屏给出当前 task/operation/attempt 与目标版本、断点步骤、已执行动作及回执、草稿或未知结果、下一步及其原文入口。每项标明依据。worker 没有发布工作记录（`work_record_ref`）时，仍从 canonical Store、原派发、冻结请求和可核验回执重建，无法重建的信息标为未知。每次恢复从当前绑定刷新，旧说明保留所属 attempt。[恢复包生成](../../src/zf/runtime/long_horizon.py) · [当前输入恢复](../../src/zf/runtime/artifact_input_recovery.py) · [工作记录](../../src/zf/runtime/work_record.py)

对每一来源分别呈现宿主历史读数（`historical_missing`）、冻结策略下 Provider 的已见证交付与缺口（`provider_delivery`）、当前 session 和压缩边界允许跳过的范围及下一页。模型当前记忆保持 `unknown`，Agent 可按本次判断主动打开指定条款。定向读达到 40/40 时，整包游标仍按自身合同计算。短摘要列不完缺口时提供可翻页的完整索引和精确读取入口。

| 唤醒时核对出的事实 | Runtime 行为及 Agent 的第一步 |
| --- | --- |
| 唯一活动 attempt 在途，target、合同、工作区及会话绑定有效，Provider 实际以 `resume` 启动 | 向原 session 交付短续行，定位原 briefing、读取顺序、输出合同和 RESUME。Agent 从未完成步骤继续。 |
| 同一 session 发生压缩 | 保留仍有效的历史交付，按当前续页边界展示缺段，按当下判断补读原文。压缩前整包完整与只完成部分定向读取应能分别解释。 |
| 已缓存 UUID 但 rollout 缺失，或 Provider 确认 session 丢失 | 按现有替换权限使用实际启动的 `fresh` 会话并记录身份，交付原协议、恢复说明及本次所需原文。冻结策略要求当前会话见证时重新交付。 |
| 旧 worker 未确认停止、身份有矛盾、operation 为 suspended / superseded，或 lease、actor、请求、target、合同已变 | 进入对应的现有受控处理。暂停的 operation 等待合法恢复，已取代的 attempt 停止续行。合法换版或新 attempt 取得新绑定，旧证据的适用范围由合同责任人判断。 |
| 原结果已正式准入、仅有草稿或预检、已有待修提交，或副作用结果未知 | 已准入结果按原 verdict 路由，含 blocked / abstained。草稿或预检只供继续工作，待修提交沿已绑定的修复路径走。未知副作用先核对宿主回执或 Git 状态，仅在既有授权允许安全重试时执行，否则交给诊断责任人。 |
| 本地副本缺失或损坏，或 worker 不共享文件系统 | 从同一冻结来源重建并比对选中正文摘要，或走同合同 CLI。冻结来源也无法核验时标为 `unavailable`。 |

**续行的执行权。** Task Pipeline 保留原 briefing 的 `Active task`、operation、dispatch、attempt、lease、Read Order 和 Output Contract。fanout Reader 还须核对原始 Kernel 派发、当前请求摘要和 manifest；CLI 的 `artifact recover` 与文件入口读同一组事实。此外，Provider 启动端记录实际选中的 `fresh` / `resume` 和 session 身份。短提示只在对应合同可以取回时发送。[Task Pipeline](../../src/zf/runtime/task_pipeline_recovery_briefing.py) · [Reader](../../src/zf/runtime/reader_respawn_continuation.py) · [Provider 启动](../../src/zf/runtime/spawn_coordinator.py)

WorkflowRuntimeCoordinator（WRC）负责派发与恢复对账。替换 worker 时沿用角色 respawn 锁和旧 transport 停止确认。生成说明后、投递提示或续发凭据前，复查唯一当前 attempt、有效 lease、当前请求及权威结果。若旧 worker 此时已有准入结果，直接按其 verdict 路由。重复唤醒只允许一个有效执行者。恢复无进展或状态不明时携带证据进入已有诊断路径。[替换屏障](../../src/zf/runtime/respawn_lease.py) · [199 的失败处理职责](199-worker-failure-recovery-and-role-handoff.md)

## 6. 实施顺序与放行条件

| 批次 | 交付边界 | 验收方式 |
| --- | --- | --- |
| 1 · 索引与文件 | 发布冻结视图，在本地 Codex 的一个直读工具上核验同次调用与返回，并在现有准入入口重放。 | 通过下表的查询、正文、交付和权限用例。记录实际工具及版本、公开调用与回执，区分真实 Provider 验证与模拟测试。 |
| 2 · 断点与投递 | 在 Task Pipeline、fanout Reader 两条恢复入口接入短索引，核对实际 Provider 启动结果。 | 通过下表的续页、替换竞争和结果路由用例，同时记录投递对象、执行授权和副作用次数。 |
| 3 · 同合同对照 | A 为现有 CLI，B 为 CLI 文本/批页，C 为工作区直接读取。固定候选、40 项必读义务、原宿主执行事实、断点、模型与推理配置。每组使用隔离状态、账本、会话及合法消费绑定，记录由此产生的请求摘要差异、缓存、顺序和试验次数。 | 逐组核对正文区间、来源归属、误报与漏报。分别计首选工具和兼容入口的调用与耗时，报告重复命令、定位错误、交付耗时、实际派发到准入耗时及可观测 input/cached tokens。 |
| 4 · 范围精简与推广 | 单独批准新切片，在同一读取方式下对比新旧合同。按已验证的角色和 Provider 推广。 | 共享约束、异常和媒体缺口保持可定位。分别给 Task 的 `acceptance_review` 和 Thin Goal Judge 配置不同必读原文，验证各自覆盖、独立账本和越权拒绝。 |

**批次 1、2 的验收用例**

| 用例 | 通过条件 |
| --- | --- |
| 同一清单内含合法 JSON、ZIP、PNG、WebM。用现场命令 `zf artifact index --attempt <attempt> --query candidate-host-execution --limit 10`，再检索仅出现在文本正文的词、单独按已知 ID 定位。 | 原命令返回目标 JSON 来源，正文命中保留原有导航语义，精确 ID 只查授权映射。媒体可通过描述符定位，解码、打开和视觉验收分别取证。损坏的合法文本快照进入重建或报错路径。 |
| 选中正文含中文、CRLF、空 JSON 字符串或仅授权的子路径；另设同字节但权限不同的来源、名称碰撞及越界链接。 | 摘要、字符范围与字节数可复算，空值绑定到具体调用。越界来源不能读取，同字节的未授权来源不能获记覆盖。副本损坏时重建原版本，冻结源校验失败时报告不可用，合法改版时建立新绑定。 |
| 同一 session 中另一条调用返回相同正文、正确调用的中段截断、已有返回但缺持久见证、原生部分交付后 CLI 补齐、全局配置变更后重试旧 attempt。 | 错 call 不获记，缺段可精确补读，欠证可定位；补齐后在线准入与封存重放一致。旧 attempt 仍按冻结策略判定。 |
| 同 lane 换人、凭据轮换或过期、挂载发布中断、封存时旧调用迟到。 | 在真实 worker 沙箱内验证访问终止、独立 lane 隔离、合法历史归属、半成品清理、Git 状态和草稿保留。迟到见证不能计入新消费者。 |
| 宿主缺口为 0 但 Provider 缺交付、缺口超过四项、worker 工作记录缺失、同 session 压缩前整包完整或不完整、换 session 或 rollout 无法核验。 | 在恢复说明中能查到所有缺项、可跳过的区间、下一页及定向原文。已见证历史可溯源，当前记忆标为未知。新会话按当前合同取回所需内容。 |
| 两个唤醒并发、生成说明后旧 worker 提交、旧 transport 未停、缓存 UUID 无 rollout、target 未变但 lease / actor / 请求已换。另分别注入已有 blocked / abstained、草稿、预检通过、待修及副作用未知。 | 每例核对实际接收者和 fresh/resume 结果，最多一个有效执行者。准入结果依原 verdict 路由；其他状态按第 5 节逐项核对，禁止重复授权或执行副作用。 |

**8×8 断点对照。** 初态固定为该 Reader 的 40 项已交付、19 条宿主命令已有执行回执、18 项可用于验收、浏览器证据因声明路径与采集索引不匹配而不可验收。以下用例共同约束三组试验。

1. 连续中断并恢复两次。两次合计新增宿主执行为 **0（含浏览器）**。每次均能从 RESUME 定位浏览器声明路径、采集记录、原执行回执及诊断责任人。同一 operation 只接纳一次有效终态。需要再次执行命令的修复须另行取得授权。
2. 将上一 lane 的交付回执直接提交给新 Reader，应拒绝。另用新 Reader 的合法消费绑定引用同一宿主事实，18 项可用证据应可验收，浏览器项继续列出缺口。原生产者 operation 可追溯，历史执行不能登记为本 Reader 新执行的 probe。
3. 再设一个声明路径、采集索引与媒体内容相符的小型正向样例，要求相应浏览器义务能凭证据通过。另改变 candidate 或合法切片版本，确认走受控新绑定；正文中段截断只补未交付部分。

**切换标准。** 安全、证据、恢复反例和业务判词须先通过。相同合同的重复配对试验中，C 应比 A/B 中表现较好者减少定位或补读负担，并改善可观测的交付成本；报告各次结果和波动。若只有导航更易用、端到端收益未测出，可先开放索引和恢复说明，严格读取继续使用已验证入口。切换只在新派发或受控重新绑定时生效；回退保留既有账本和冻结来源。真实无人干预完成整条 workflow 另行取证。
