# 216 持久交接的实施与验证

在 215 worktree 从提交 `6e83a65f` 接续。215 的阶段义务提供地址，216 由内核准入消息、核对当前消费者并记录交付事实。启用开关为项目 `zf.yaml` 的 `runtime.durable_mailbox.enabled`。每个业务 Task、候选、Judge 和交付各自使用原合同。下表列出逐项复验入口。工程验收和真实无人窗口分别记录。[216 规格](../design/specs/216-durable-agent-mailbox-and-handoff.md)

| 条款 | 可观察的结果 | 复验入口 |
|---|---|---|
| AC-01 | 同一逻辑发送者重放只接受一次，改变收件人或内容产生冲突 | `tests/test_durable_mailbox.py` |
| AC-02 | 接受之前崩溃留下的正文不会进入收件箱，接受后能重建 | `tests/test_durable_mailbox.py` |
| AC-03 | 真实 Proposal 准入、尚无实例的义务接收邮件，WRC 派发后原邮件可取 | `tests/test_durable_mailbox_roster.py` |
| AC-04 | 读后换代的旧凭据无法再 ACK 或回答，提交时检查并发换代 | `tests/test_durable_mailbox.py`、`tests/test_assistance_mailbox_workflow.py` |
| AC-05 | 原 request 重取同批正文与回执，新到的邮件留给新请求，缺件显式失败 | `tests/test_durable_mailbox.py` |
| AC-06 | 两个 Worker、繁忙 OA、进程中断及重新对账共用原求助 ID，答案有当前 owner | `tests/test_assistance_mailbox_workflow.py` |
| AC-07 | 预留后中断与效果未知分别处理。可证未发送的提示需要原 Run 恢复后再试 | `tests/test_assistance_mailbox_workflow.py` |
| AC-08 | 已接手待答、授权等待及接手者消失分别保留责任和恢复出口 | `tests/test_assistance_mailbox_workflow.py` |
| AC-09 | 正文受当前凭据与 Run 约束，工作台、事件、目录与会话输出通道保护正文 | `tests/test_durable_mailbox.py`、`tests/test_stage_team_mailbox.py`、`tests/test_assistance_mailbox_workflow.py` |
| AC-10 | 答案与交付 ACK 不改变 Task、独立 Verify 或 Judge 状态 | `tests/test_durable_mailbox.py`、`tests/test_assistance_mailbox_workflow.py` |
| AC-11 | 原义务同版本的后继可继续，跨合同或失效凭据须重新核对 | `tests/test_durable_mailbox_roster.py`、`tests/test_assistance_mailbox_workflow.py` |
| AC-12 | 多收件者单独计回执，两个消费通道竞争同一份逻辑交付 | `tests/test_durable_mailbox.py` |
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
