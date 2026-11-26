# 216 持久交接的实施与验证

在 215 worktree 从提交 `6e83a65f` 接续。215 的阶段义务提供地址，216 由内核准入消息、核对当前消费者并记录交付事实。启用开关为项目 `zf.yaml` 的 `runtime.durable_mailbox.enabled`。每个业务 Task、候选、Judge 和交付各自使用原合同。下表列出逐项复验入口。工程验收和真实无人窗口分别记录。[216 规格](../design/specs/216-durable-agent-mailbox-and-handoff.md)

| 条款 | 可观察的结果 | 复验入口 |
|---|---|---|
| AC-01 | 同一逻辑发送者重放只接受一次，改变收件人或内容产生冲突 | `tests/test_durable_mailbox.py` |
| AC-02 | 接受之前崩溃留下的正文不会进入收件箱，接受后能重建 | `tests/test_durable_mailbox.py` |
| AC-03 | 真实 Proposal 准入、尚无实例的义务接收邮件，WRC 派发后原邮件可取 | `tests/test_durable_mailbox_roster.py` |
| AC-04 | 读后换代的旧凭据无法再 ACK 或回答，提交时检查并发换代 | `tests/test_durable_mailbox.py`、`tests/test_assistance_mailbox_workflow.py` |
| AC-05 | 原 request 重取同批正文与回执，新到的邮件留给新请求，缺件显式失败 | `tests/test_durable_mailbox.py` |
| AC-06 | 两个 Worker、繁忙 OA、进程中断及重新对账共用原求助 ID，答案有当前 owner | `tests/test_assistance_mailbox_workflow.py`、`tests/test_mailbox_cross_role_acceptance.py` |
| AC-07 | 预留后中断与效果未知分别处理。可证未发送的提示需要原 Run 恢复后再试 | `tests/test_assistance_mailbox_workflow.py` |
| AC-08 | 已接手待答、授权等待及接手者消失分别保留责任和恢复出口 | `tests/test_assistance_mailbox_workflow.py` |
| AC-09 | 正文受当前凭据与 Run 约束，工作台、事件、目录与会话输出通道保护正文 | `tests/test_durable_mailbox.py`、`tests/test_stage_team_mailbox.py`、`tests/test_assistance_mailbox_workflow.py` |
| AC-10 | 答案与交付 ACK 不改变 Task、独立 Verify 或 Judge 状态 | `tests/test_durable_mailbox.py`、`tests/test_assistance_mailbox_workflow.py`、`tests/test_mailbox_cross_role_acceptance.py` |
| AC-11 | 原义务同版本的后继可继续，跨合同或失效凭据须重新核对 | `tests/test_durable_mailbox_roster.py`、`tests/test_assistance_mailbox_workflow.py` |
| AC-12 | 多收件者单独计回执，两个消费通道竞争同一份逻辑交付 | `tests/test_durable_mailbox.py`、`tests/test_mailbox_cross_role_acceptance.py` |
| AC-13 | Dashboard 与 CLI 同游标，显示无实例成员、待接手、待答与恢复责任；只读查看不写 ACK | `tests/test_stage_team_mailbox.py`、`web/tests/workflow-team-live.spec.ts` |
| AC-14 | 试点停用可排空已接纳邮件，原问题与未知交付仍可定位 | `tests/test_assistance_mailbox_workflow.py::test_disabling_then_reenabling_pilot_preserves_each_existing_assistance_receipt` |
| AC-15 | 归档/重建时保持同一逻辑交付，缺正文单列为证据不可用 | `tests/test_durable_mailbox.py::test_archive_replay_recovers_receipts_without_index_and_reports_missing_body`、`tests/test_durable_mailbox_index.py` |

### 操作者与消费者

Worker 首次派发或恢复时，briefing 给出本 attempt 的只读收件入口。查看消息目录、取一批正文、显式签收，以及接手/回答问题是独立动作。OA 使用原 assistance 队列及角色凭据。CLI 的实际参数见两份生成索引。[CLI 命令索引](../manual/reference/cli-command-index.md)

已批准的宿主命令、发布路径、角色和模型在真实试点启动前核对。父 Run 终态仅处置自身诊断链。采集对照按正式集成回执计算业务任务，并单列归属未知的 Web 控制动作及宿主记录缺口。[资格协议](spec215-qualification.md)

通知的 `confirmed` 由实际发送的适配器返回写入证明。tmux 在指针和提交键成功写入目标 pane 后提供该证明。stream-json 后台排队、tmux dry-run 和未提供写入证明的适配器保留 `unknown`。恢复时 OA 可通过收件箱领取同一问题，watcher 不会自动重发该通知。正文的签收与答复另有独立回执。[通知边界与重启回归](../../tests/test_assistance_mailbox_workflow.py)

### 索引与授权验证

在独立临时状态中，16 个线程投递 112 封消息，另有 60 条无关工具事件。B 是 3 封的收件人，C 是 112 封的收件人。每页取 2 项时，B 的五次温读分别解码 [10, 9, 9, 9, 9] 条原账本记录，中位 1.05 毫秒。C 的五次温读解码 [118, 118, 118, 118, 118] 条，中位 3.97 毫秒。十次温读调用全账本读取 0 次。此测量使用真实 EventLog、SQLite 和同一台机器的临时状态，耗时会随数据与机器负载变化。分页仍需核对命中收件人的逻辑总数。丢索引、被修改的定位行、归档段重建与缺件在独立回归中验证。

父 Run 的 `preserve/cancel/continue` 在共享 watcher 上按 Run 处置，控制面重新装配会带入项目既有的收件箱、宿主验收及发布授权。OA 首次合成与按需 RM 反思的测试核对实际 provider 请求中的模型及推理参数。[诊断处置测试](../../tests/test_diagnosis_run_disposition.py) · [合成调用测试](../../tests/test_workflow_synthesis.py)

### 真实交接与启动故障

在 `6dae7720`，两个真实 Luna Worker 回合和一个 Astra OA 回合完成了提问、签收、答复和换代取回。提问者在本次答复中取得此前未告知它的校验值。旧 attempt 与已撤销凭据均被拒，业务 Task 仍为 `in_progress`。同一故障脚本的确定性对照中，预留后、写通知前中断，原路径在观察期内答复 0/2，试点答复 2/2，每题通知一次。正常路径两边均答复 2/2；试点多记录 12 个事件。真实模型时间、模拟等待时间及样本限制分别记录。[真实交接回执](../../reports/spec216/real-mailbox/qualification.json) · [配对性能报告](../../reports/spec216/engineering/paired-mailbox-performance.md)

首次 8×8 启动时，两个项目都在 Plan 前重现同一问题。OA 语义结果已经接纳，本地 PrdFlow 组装遗漏已登记的核验委托，CLI 随即将提案构建失败显示为仍在合成。修复覆盖本地与注册控制器入口，并通过原请求和原受理结果重试提案组装。确认框检查覆盖持久通知粘贴前和提交 Enter 前两个边界；部分输入后失去可见性或出现确认框时保留 `unknown`。交互式 PTY 的末次取屏与发键属于两个系统调用，额外检查不能提供原子输入保证。[启动和无人窗口记录](../../reports/spec216/real-8x8/qualification.md)

### 从 16 项真实业务暴露的恢复问题

测试使用同一套 8 个既有文本模块和 8 个待实现数列模块，Impl 与独立 Task Verify 各配置 8 个执行位。OA 使用 Astra，其余角色使用 Luna。Plan 批准前的恢复、批准后的操作介入和业务交付分别计量。

| 实跑发现 | 修复或判定 |
| --- | --- |
| OA 结果已受理，提案组装仍报错，页面显示仍在合成 | `78ddf955` 对齐本地与注册入口，原请求复用既有的 OA 结果。 |
| Planner 现场未能区分只读 Reader 与写者，错误同时涉及多项任务或多个 Reader 时难以定位 | `6974c5ac` 和 `efa9244f` 修正写者资格和 Reader 路径诊断，保留每项原合同。 |
| RM 已实际返回等待，后续诊断却显示没有决策者。诊断子 Run 错误继承 canary 的 60 次用量采样限制 | `04b07015` 绑定已有 RM 收据。`edc41b6c` 核对生产父 Run 的实际限额及诊断请求自身的授权。A 在单次获准的 30 分钟截止时正确结束，留下诊断未知及后续责任。 |
| 原 B 因操作员在只有 proposed 的情况下重派而失去资格 | `efa9244f` 在受控动作入口拒绝该路径。原 B 保留为实验偏差，另起独立的 B2。 |
| B2 反复校验 16 项任务，根因被同源的多份报错遮住 | 在原提交的隔离副本上，把顶层所需端口和两项 Reader 字段分别修正，重现了 98→2→0。但过预检的副本仍把 8 个只读审计派成普通开发，不能据此批准或认定交付。 |

工程验证在 `efa9244f` 的自动影响闭包中取得 18,921 个 pytest 通过、19 项跳过，另有串行、前端、53 项前置检查及 10 项流程 smoke。三项确定性 8×8 测试覆盖调度合同，实际业务并发需要当前 Run 的原生回合重叠。[工程日志](../../reports/spec216/engineering/dev-verify-efa9244f.log) · [真实 8×8 逐阶段记录](../../reports/spec216/real-8x8/qualification.md)

直接发给 Worker 的问题可由 Run Manager 接手诊断。接手后的 WRC 终止回执，或具有相同 Run、执行与原生 session/turn 的提供方结束记录，会关联原消息和接手回执。存在授权继任、有效等待或 Run 暂停时，系统保留原责任。问答记录继续开放，产品验证仍由原阶段负责。缺少执行绑定的原生观测保留为未知。[直接问答回归](../../tests/test_peer_question_liveness.py)


### 验收边界与实跑断点

两个 Worker 等待 OA 时，同一 Run 的第三项独立任务可经常规调度入口获得派发并进入执行。Judge 会拒绝以已签收的“全部完成”消息充当正式验收依据。同一操作提交已准入的上游证据可通过。通知指针携带的命令和另一个独立 CLI 进程并发领取时只产生一次有效消费，已关闭的问题拒绝迟到的接手与答复。这三项通过实际调度器、Judge 准入和 CLI 进程做确定性验证。[跨角色回归](../../tests/test_mailbox_cross_role_acceptance.py)

真实 Codex 定向试验由 Luna 提问、Astra 签收回答及换代后的 Luna 取回答案组成，旧 attempt 和已撤销凭据均被拒。真实 tmux 六个输入场景另有字节级证明。Claude 登录检查返回未登录，Claude 原生模型及其 stream-json 端到端资格仍待验证。独立 Codex 交接试验未运行实际业务 Verify 或 Judge。工作台的 Docker 浏览器用例覆盖当前与历史义务、隐私与无隐式签收。[真实 Codex 收据](../../reports/spec216/real-mailbox/followup/qualification.json) · [tmux 边界](../../reports/spec216/engineering/real-tmux-submit/summary.md) · [Claude 环境](../../reports/spec216/engineering/claude-qualification-capability.json) · [Dashboard 浏览器回执](../../reports/spec216/engineering/browser/frozen-cfa113d9e20d/provenance.json)

A 的 RM 自主选择并重派了精确 Reader。Planner 预检曾显示零错，但正式提交缺少本 attempt 的必读凭证。随后旧回合结束信号先于已准许的校正处理。两项均已加入生产预检、WRC 调度与回归。A 最终停在修复候选的验证目录与隔离 checkout 不符，运行时已向 Owner 送达决定。B2 超时诊断曾被较早的静默状态挡住。在 `cfa113d9` 的真实父控制面，RM 已自动消费同一诊断并完成反思，随后送达 Owner 等待。两组的业务 Plan 均未获批，业务交付均为 0/16。[输入预检重放](../../reports/spec216/engineering/plan-readiness-fa319166/real-a-cli.json) · [A 原断点](../../reports/spec216/real-8x8/A/owner-escalation-before-plan.json) · [B2 真实恢复链](../../reports/spec216/real-8x8/B2/cfa113d9-diagnosis-continuation.json) · [配对资格清单](../../reports/spec216/real-8x8/qualification-outcomes.json)

### 冻结版本的复验

在 `ed208dd2`，确定性影响闭包有 18,987 项通过、19 项按标记跳过和 14 条警告，另有 4 项串行测试、前端类型检查和单元测试、53 项合并门禁与 10 项流程 smoke。修复前的 16 个 Plan 回归用例都曾在原实现失败，修复后同时保留语义诊断与本 attempt 的必读约束。[冻结版工程闭包](../../reports/spec216/engineering/dev-verify-ed208dd2.log) · [A 输入的受控回放](../../reports/spec216/engineering/plan-readiness-compatibility/real-a-cli.json)

在一个独立的真实模型试验里，Worker 发出“已完成”的邮件，Judge 读到独有标记并明确签收。随后同一个 Judge 使用同一 attempt 的凭据两次调用公开 `zf result submit`。仅提交邮件正文时，两处证据引用均被拒，退出 1。改为正式准入的本地 Verify 结果后，退出 0，Judge 操作才完成。该对照由三个真实 Luna high 回合执行，上游工程用例只含一条本地断言。16 项产品的资格另计。[Judge 正负对照及角色绑定](../../reports/spec216/real-mailbox/final-gate-ed208dd2/qualification.json)


### 产品级无人验证结果

两次准备阶段试验保留了各自正式的人工决定，A 和 B2 均未获得可批准的 Plan。原 B 曾由测试助手在重派提案只有 proposed 状态时调用受控动作，已排除。A 和 B2 之间的实际团队义务摘要也有差异，无法据此估算常驻 RM 的净收益。[配对结果与排除原因](../../reports/spec216/real-8x8/qualification-outcomes.json)

最终冻结试验 C 从新的业务基线和 Run 启动。OA 使用 Astra，其他角色使用 Luna。产品扫描完成，技术扫描在生成 operation 前拒绝了目录 `app/`。原提案曾被标作可批准，项目在该位置确实有目录。19:54:52 UTC，按需 RM 自主启动诊断，20:24:55 收到超时结果，20:25:17 向 Owner 送出需要授权的决定。实验期间没有从外部修订 C 的计划、重派 Worker 或代交结果。[原始技术扫描诊断](../../reports/spec216/real-8x8/C/tech-scan-input-diagnosis.json) · [正式终点](../../reports/spec216/real-8x8/C/final-qualification.json)

C 的 16 项业务都未派发，未取得独立验收。没有实际 8 Impl 与 8 Task Verify 重叠，Candidate、产品 Docker 验收、Judge、Goal 和本地交付均未进入。批准后的无人窗口未开启。诊断转交与升级得到实证，端到端无人交付未通过。16 项覆盖和各自的证据状态均单独保留。[逐项覆盖](../../reports/spec216/real-8x8/C/module-qualification.json)

后续工程修复 `e5ba0235` 用相同文件及可用 Markdown 锚点规则检查 Plan 的项目实物引用。C 原提案在隔离重放中提前被拒，保留同一合同只将 `app/` 改为合法文件的对照能够准入。来源变化后，提交预览与两个正式授权入口均报 409，原展示快照仍可查。该版本的确定性影响闭包有 18,994 项通过、19 项跳过、13 条警告，串行、前端、合并门禁和流程 smoke 均通过。C 实际运行在 `ed208dd2`。把这类确定性错误反馈给原 OA，自主产生并重新核准修正提案，还缺真实的受控闭环证明。[修复与公开入口回放](../../reports/spec216/engineering/stage-team-input-e5ba0235/qualification.json) · [最终工程闭包](../../reports/spec216/engineering/dev-verify-e5ba0235.log)

### Plan 前的来源纠正与交接

Workflow Synthesis 的 OA 给出不可读取的项目实物来源时，准入器将原候选、具体成员、义务、引用和拒绝原因保存在当前请求的不可变凭证中。同一 OA 线程按该成员的问题选择可读文件或报告阻塞。修正只作用于被拒引用，仍需重新校验完整候选并进入原 Proposal 审批。投递和答复分别有回执，可按请求版本和摘要重建。

已获批准的 PRD 在派发扫描前发现来源失效时，启用阻塞型 `plan_source` 的 OA 策略可以按 Run、fanout 和成员义务接手。原声明可用 `input_alternatives` 将一个输入映射到已批准的具体备选引用。OA 选择后，Kernel 检查原提案、目标、访问范围和文件可读性。同一成员有多个失效来源时，每个原始引用独立求决。重启后从事件账本重建已选来源，实际派发前逐个复核已绑定的文件摘要。已选文件再度漂移时，保留原决策并交由 Run Manager 诊断。其他成员的有效输入及已准入的扫描回执继续使用。修正失败、无授权备选或 OA 升级时，进入现有失败交接。工作流保持原扫描汇总条件与独立验收职责。

正式配置应用使用已批准的候选正文，并从目标项目的配置路径解析相对 `profile_sources`。新提案冻结来源摘要；旧提案按冻结的有效配置核对。源文件漂移会拒绝应用，修复后可用新的受控动作继续处理原提案。

只有一个业务任务时，Planner 保留独立 Candidate 验收与必验旅程，并在产品验收描述和读取者分工中分别使用空的 `integration_checks` 和 `integration_check_ids`。跨任务检查的每一项仍需引用至少两个不同的已批准任务。

Plan 汇总与评审核对各份输入的来源组、文件标识、引用和摘要。业务原文和需求登记可以共用来源组；同样内容的两份独立文件仍分别进入交接，同一文件的不同条款按各自选择器验收。

一个聚合角色可同时持有调度器的 TaskAttempt 和独立的工作流操作尝试。briefing 用 TaskAttempt 上报心跳，并用当前 Run、任务、角色及操作共同绑定的尝试地址查收交接。CLI 在读取时再次核实角色和操作状态。

工作台按当前 Run 和 fanout 的证据展示派发前拒绝、等待纠正、来源已重新绑定及实际领取。查看历史运行时，若拒绝回执没有指明接手人，详情显示“待核实”。重新绑定回执只说明可再次派发，只有真实派发才有执行尝试，只有验收回执才进入准入计数。
