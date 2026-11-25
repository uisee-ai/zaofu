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

### 索引与授权验证

在独立临时状态中，16 个线程投递 112 封消息，另有 60 条无关工具事件。B 是 3 封的收件人，C 是 112 封的收件人。每页取 2 项时，B 的五次温读分别解码 [10, 9, 9, 9, 9] 条原账本记录，中位 1.05 毫秒。C 的五次温读解码 [118, 118, 118, 118, 118] 条，中位 3.97 毫秒。十次温读调用全账本读取 0 次。此测量使用真实 EventLog、SQLite 和同一台机器的临时状态，耗时会随数据与机器负载变化。分页仍需核对命中收件人的逻辑总数。丢索引、被修改的定位行、归档段重建与缺件在独立回归中验证。

父 Run 的 `preserve/cancel/continue` 在共享 watcher 上按 Run 处置，控制面重新装配会带入项目既有的收件箱、宿主验收及发布授权。OA 首次合成与按需 RM 反思的测试核对实际 provider 请求中的模型及推理参数。[诊断处置测试](../../tests/test_diagnosis_run_disposition.py) · [合成调用测试](../../tests/test_workflow_synthesis.py)
