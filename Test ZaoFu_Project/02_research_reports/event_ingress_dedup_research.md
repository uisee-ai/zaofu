# K1 / EVT-05：事件入口去重与生产消费者调研

> 文档定位（2026-09-30）：历史调研，正文“当前/建议/尚未实现”仅适用于各段所记时点，不是最新执行队列。现行方案见 [ZaoFu_TestKit.md](../03_plans_and_steps/ZaoFu_TestKit.md)，执行仍按既有暂停/跳过边界；本次不重跑或改写历史结果。
>
> 当前对齐入口：`tests/conformance/test_zaofu_testkit_s2_event_ingress.py`。EVT-05 检测入口已存在；去重要求、raw occurrence 写入与已检出生产问题不能混为一谈。

2026-09-14；基于工作树 HEAD `142a62a` 的源码、现有测试与本目录 Issue 记录。按 research 技能只读追溯一手来源；本轮仅新增此文件，未运行测试、临时探针、Provider、容器或远端操作。以下“推断”不是本轮复现结果。

## 1. 结论与边界

原 [TestKit §6 EVT-05](</home/uisee/zaofu_v7/awesome-zaofu/New_function/Test ZaoFu_Project/03_plans_and_steps/ZaoFu_TestKit.md:271>) 明确要求“只接受一次；projector 不重复运行；attempt 不重复增长”。当前 `EventWriter.append()` 与 `EventLog.append()` 均无按 ID 去重分支；默认 projector 每次追加后运行，因此 helper-only 测试不能宣称该条已完成。[Writer.append](/home/uisee/zaofu_v7/src/zf/core/events/writer.py:87)、[_append_with_projectors](/home/uisee/zaofu_v7/src/zf/core/events/writer.py:113)、[Log.append](/home/uisee/zaofu_v7/src/zf/core/events/log.py:77)。

必须把三件事分别验证：入口是否接受重复事实、真实投影是否重复消费、canonical Task/Attempt 是否增加副作用。一个层次通过不证明其他层次。尤其不能把 `Task.retry_count`、scheduler TaskAttempt 条数和 fanout payload 中的 `attempt` 当成同一计数。[业务 retry](/home/uisee/zaofu_v7/src/zf/runtime/housekeeping.py:569)、[TaskAttempt.ensure_for_dispatch](/home/uisee/zaofu_v7/src/zf/core/state/task_attempts.py:118)、[fanout attempt 元数据](/home/uisee/zaofu_v7/src/zf/runtime/fanout.py:825)。

## 2. 输入身份必须分开

| 输入 | EVT-05 应检验的合同 | 当前源码能支持的判断 |
|---|---|---|
| 同 ID、同完整内容重送 | 一次 accepted occurrence；重复输入不再次驱动投影或业务副作用 | Writer/Log 每次追加；无原子 check-and-append。须正式测试确认差异 |
| 同 ID、不同内容 | 拒绝冲突或明确隔离；原 accepted 内容不可被第二份替代 | 两份均进入账本；EventIndex 内存按 ID 后写覆盖。没有显式冲突裁决 |
| 同内容、不同 ID | 保留两个独立 occurrence；不能按 payload 全局合并 | `ZfEvent` 默认生成新 ID；日志保留两次。特定业务可能另按 fanout/dispatch 身份幂等 |
| schema blocking 后重试 | 区分“原事件未接受”与“新拒绝事实”；不能把拒绝记录当原事件已接受 | 原事件不落盘，每次生成新 ID 的 `discriminator.failed`，payload 保存 blocked_event_id |
| schema warning | 合法允许原事实加诊断事实，不能断言每次 Writer 调用账本只能有一行 | 原事件后追加一个新 ID 的 `event.schema.violated`，两个事件均走 projector runner |

来源：[事件 ID 生成](/home/uisee/zaofu_v7/src/zf/core/events/model.py:11)、[事件字段](/home/uisee/zaofu_v7/src/zf/core/events/model.py:19)、[Index.observe](/home/uisee/zaofu_v7/src/zf/core/events/index.py:62)、[warning](/home/uisee/zaofu_v7/src/zf/core/events/writer.py:119)、[blocking](/home/uisee/zaofu_v7/src/zf/core/events/writer.py:144)。

“同内容”需固定 `ts/origin/causation_id/correlation_id`，比较完整序列化值，而非只比较 payload。Writer 会补 origin，并从当前任务最后事件补 causation；重复提交原始对象可能因此产生自指或不同父事件。建议先写固定 parent，再让重送输入显式引用它；用 `ZfEvent.from_dict()` 重建相同值，避免共享可变 payload。[enrichment](/home/uisee/zaofu_v7/src/zf/core/events/writer.py:198)、[origin](/home/uisee/zaofu_v7/src/zf/core/events/writer.py:89)、[from_dict](/home/uisee/zaofu_v7/src/zf/core/events/model.py:43)。

## 3. Writer、Log、Index、projector 的实际合同

- `EventLog.append` 持路径锁，轮转后编码并写一行，再尽力更新 Index；锁保护追加，不检查重复 ID。[append](/home/uisee/zaofu_v7/src/zf/core/events/log.py:77)。
- `read_all` 将 archive 与 active 按 occurrence 拼接，append-fold 也是旧元组加新 tail；`read_from_offset` 每条完整行均解码返回。这些读路径不做 ID 去重。[read_all](/home/uisee/zaofu_v7/src/zf/core/events/log.py:316)、[fold](/home/uisee/zaofu_v7/src/zf/core/events/log.py:370)、[read_from_offset](/home/uisee/zaofu_v7/src/zf/core/events/log.py:220)。
- Index 是可丢弃缓存：`_event_by_id[event.id] = asdict(event)`，任务 ID 列表只避免相邻重复；cold load 不恢复完整 payload。warm task query 看起来只有一条，不证明账本唯一；同 ID 冲突可能呈现后写内容。[observe](/home/uisee/zaofu_v7/src/zf/core/events/index.py:71)、[cold load](/home/uisee/zaofu_v7/src/zf/core/events/index.py:163)、[task query fallback](/home/uisee/zaofu_v7/src/zf/core/events/log.py:410)。
- `ProjectorRunner.run` 仅过滤类型、调用 handler、记录 ok/failed/skipped；没有 seen-ID 集合。projector 异常不回滚已追加事件。[runner](/home/uisee/zaofu_v7/src/zf/core/events/projectors.py:46)、[异常合同测试](/home/uisee/zaofu_v7/tests/test_event_writer_projectors.py:87)。
- 默认注册真实 `fanout_manifest` 与 `stage_report`。前者按全部账本重建；重复 child dispatched 可能只覆盖相同 child 状态，看起来不变，但 slot_events 等列表会追加两项。只看最终 child.status 不足以证明没重复投影。[默认注册](/home/uisee/zaofu_v7/src/zf/core/events/writer.py:260)、[manifest 重建](/home/uisee/zaofu_v7/src/zf/runtime/fanout.py:333)、[slot_events](/home/uisee/zaofu_v7/src/zf/runtime/fanout.py:543)。

低层 raw append 当前是 occurrence 写入原语；用它主动制造重复，再发现重复，首先只是验证检测器有效。不能仅据此定性轮转或 consumer 有 bug。EVT-05 未完成的证据应来自明确 Writer 入口与真实投影/业务影响，原始账本模型仍保留输入次数，不能为变绿偷偷去重 expected。[既有研究边界](</home/uisee/zaofu_v7/awesome-zaofu/New_function/Test ZaoFu_Project/02_research_reports/eventlog_model_research.md:7>)、[S2 检测器测试](/home/uisee/zaofu_v7/tests/conformance/test_zaofu_testkit_s2.py:70)。

## 4. 哪些生产入口复用什么身份

1. `zf emit` 未注册 `--event-id`；`--payload-file` 只读取 payload，`--dispatch-id` 不是事件 ID。`run_emit` 每次构造新的 `ZfEvent` 再走 Writer；重复 CLI 文本不能作为同 event ID 重送输入。[参数](/home/uisee/zaofu_v7/src/zf/cli/events.py:35)、[构造/追加](/home/uisee/zaofu_v7/src/zf/cli/events.py:193)。
2. 已存在事件可经真实 watcher 路线重复送给消费者：`start._on_event` 从行恢复完整 `ZfEvent`，`_process_watched_event` 调用 `orchestrator.run_once(events=[event])`。因此使用真实 run_once 接受同一序列化事件是有效生产消费者测试，不需启动 tmux 或 Provider。[恢复事件](/home/uisee/zaofu_v7/src/zf/cli/start.py:1449)、[调用 consumer](/home/uisee/zaofu_v7/src/zf/cli/start.py:591)。
3. Feishu `ingest_feishu_event` 解析的是 `FeishuWebhookEvent`；message 路径依据 app_id/message_id（缺 ID 时含发送者、chat、create_time、text 的摘要）在持久化 `IdempotencyStore.check_and_record` 防重。然后 `_post_to_channel` 新建 `feishu.inbound.received`，把它作为 `requested` 传给 ControlledActionService；外部消息 ID 留在 payload/refs，没有直接变为 `ZfEvent.id`。[入口](/home/uisee/zaofu_v7/src/zf/cli/feishu_consume.py:41)、[外部 key](/home/uisee/zaofu_v7/src/zf/cli/feishu_consume.py:56)、[防重](/home/uisee/zaofu_v7/src/zf/cli/feishu_consume.py:145)、[实际 command](/home/uisee/zaofu_v7/src/zf/cli/feishu_consume.py:191)、[原子 key 写入](/home/uisee/zaofu_v7/src/zf/integrations/feishu/storage.py:22)。
4. Feishu 卡片/命令 `_handle_event_data` 也使用独立 idempotency 文件；OpenClaw inbound 则扫描已接受的 bridge key，重复时仍新增一个 skipped 审计事件。不能用“账本总行数完全没变”验证所有 Feishu 重送路径。[命令去重](/home/uisee/zaofu_v7/src/zf/cli/feishu.py:1404)、[bridge 去重/skip](/home/uisee/zaofu_v7/src/zf/runtime/openclaw_feishu_inbound.py:120)、[已有真实入口测试](/home/uisee/zaofu_v7/tests/test_openclaw_feishu_bridge.py:601)。
5. 本工作树 `src/`、`tests/` 定向检索未找到 `task_workflow_ingress` 模块或符号；实际相邻文件是 `runtime/task_workflow_plans.py`。不能引用不存在的旧路径来宣称某 Task workflow 入口保留原 `ZfEvent.id`。当前可核实的原事件传递是第 2 项 watcher→run_once，Feishu `requested` 是第 3 项新生成的事件。[现存 Task workflow 文件](/home/uisee/zaofu_v7/src/zf/runtime/task_workflow_plans.py:1)。

## 5. 最小真实验证建议（主线程执行，本轮未执行）

### A. Writer → 默认 stage report：入口与真实投影

Given：隔离 state_dir，用 `TaskStore.add()` 创建一个 Task，默认 `EventWriter(EventLog(...))`；固定 parent 和合法 `dev.build.done` 内容。可沿用 [stage report 夹具](/home/uisee/zaofu_v7/tests/test_stage_reports.py:16)，无需 runtime/Provider。

Step：第一次 Writer.append → verify：账本该 ID 一次，真实 `artifacts/stage-reports/impl-report.json` 的 `events` 及 `summary.event_count` 包含该事件一次。

Step：同一完整事件再次 Writer.append → verify：EVT-05 期望该 ID 仍一次，投影内容/该事件计数不变。独立读取账本原行、`read_all()` 和落盘 JSON，禁止只从 Index 或自行 `set(ids)` 得出结果。源码推断当前会得到两条原事件及 report.event_count=2。[真实构建](/home/uisee/zaofu_v7/src/zf/runtime/stage_reports.py:87)、[事件列表](/home/uisee/zaofu_v7/src/zf/runtime/stage_reports.py:131)、[计数](/home/uisee/zaofu_v7/src/zf/runtime/stage_reports.py:173)、[原子落盘](/home/uisee/zaofu_v7/src/zf/runtime/stage_report_io.py:15)。

Step：独立夹具中仅换新 ID，保留其他字段 → verify：账本与报告各有两个事件。这是防止按内容错误合并的正向控制，不要求业务一定发生两次 dispatch。

Step：同 ID 只改 payload 一个标记 → verify：第二份不能成为 accepted 业务事实，第一份内容仍完整。记录实际是否异常、拒绝事件或静默接受；当前无已定义冲突异常类，不应编造类名作为 oracle。

如果必须精确证明“projector 不重复运行”，可对真实 `project_stage_report_for_event` 包一层仅记录调用、继续调用原函数的透明 wrapper，配合落盘 JSON 断言；不能注入自去重 spy，也不能仅用文件 mtime 判断。现有 [default runner 测试](/home/uisee/zaofu_v7/tests/test_event_writer_projectors.py:40) 替换了两个 projector，只证明接线。

### B. Writer → Orchestrator.run_once → TaskStore / TaskAttemptStore

优先复用现成 rework 模型的合法 review Task、active Session、mock roles/transport：一条 `review.rejected` 经 Writer 入账，真实 `run_once(events=batch, consumed_offset=boundary)` 消费；再把同一事件对象的序列化副本重送给同实例。正向首次应实际触发一次 rework，不能以“前置条件没生效导致全部零”通过。[合法夹具](/home/uisee/zaofu_v7/tests/conformance/test_zaofu_testkit_s11_rework_model.py:85)、[生产消费](/home/uisee/zaofu_v7/tests/conformance/test_zaofu_testkit_s11_rework_model.py:155)、[重复动作](/home/uisee/zaofu_v7/tests/conformance/test_zaofu_testkit_s11_rework_model.py:259)。

Step：首次消费后记录 Task.retry_count、active_dispatch_id、TaskAttemptStore 中全部 attempt/dispatch/lease ID 和 task.rework.requested/task.dispatched 数量 → verify：重复消费后上述值不增加，所有比较从真实 Store/账本独立读取。[已有 Store oracle](/home/uisee/zaofu_v7/tests/conformance/test_zaofu_testkit_s11_rework_model.py:270)。

Consumer 合同并非全局永久去重：`Orchestrator` 用 50,000 项实例内 `BoundedIdSet`，消费前检查，业务分支完成后记录 ID，批次末保存 offset；cold instance 不继承 seen 集合。[初始化](/home/uisee/zaofu_v7/src/zf/runtime/orchestrator.py:421)、[消费入口](/home/uisee/zaofu_v7/src/zf/runtime/orchestrator.py:2448)、[checkpoint](/home/uisee/zaofu_v7/src/zf/runtime/orchestrator.py:2738)、[有界窗口说明](/home/uisee/zaofu_v7/src/zf/core/events/dedupe.py:1)。不要从一个同实例 case 外推所有事件、无限旧重送或所有恢复边界。

TaskAttempt 防重另依赖 `(run_id, task_id, dispatch_id)` 派生的 attempt ID，已存在则返回 `created=False`；结果结算要求完整当前身份且仅接受 active attempt。即使全局 event ID 重复入账，某 attempt 仍可能保持一次；因此 `attempt 数量=1` 不能单独证明 EVT-05 完成。[身份生成](/home/uisee/zaofu_v7/src/zf/core/state/task_attempts.py:623)、[ensure 去重](/home/uisee/zaofu_v7/src/zf/core/state/task_attempts.py:159)、[结果合同](/home/uisee/zaofu_v7/src/zf/runtime/task_attempt_runtime.py:365)。

### C. schema retry：单独限定，不与 A 混统计

固定一条违反 required 字段的输入，warning 模式验证“原事件 + warning”，blocking 模式验证仅 replacement；重复 blocking 时按 `blocked_event_id` 关联拒绝尝试，不能把两个不同 ID 的 rejection 误报为重复原事件。修正 payload 的重试应证明最终合法事件可被接受，不能把第一次拒绝当永久占用 ID；这是建议新增合同，需要与“已接受 ID 冲突”区分。[schema 现有测试](/home/uisee/zaofu_v7/tests/test_event_writer_schema.py:97)、[blocking 实现](/home/uisee/zaofu_v7/src/zf/core/events/writer.py:144)。

拒绝后的证据补交可能合法重派；只检查“不额外计业务 rework 预算”，不能统一断言所有 rejection 都不产生新 attempt。[既有 evidence reissue 正向合同](/home/uisee/zaofu_v7/tests/conformance/test_zaofu_testkit_s11_rework_model.py:402)。

## 6. Issue 去重与退出条件

| 既有 Issue | 已覆盖边界 | 本轮区分 |
|---|---|---|
| [001](</home/uisee/zaofu_v7/awesome-zaofu/New_function/Test ZaoFu_Project/04_audit_and_issue_ledger/ZaoFu_issue.md:1193>) | 签名/append-fold 缓存掩盖前缀篡改 | A 不改已有文件，不用签名篡改，不能重复归因 |
| [013](</home/uisee/zaofu_v7/awesome-zaofu/New_function/Test ZaoFu_Project/04_audit_and_issue_ledger/ZaoFu_issue.md:1267>) | 归档碰撞崩溃复制旧 occurrence | A 是主动同 ID 重送；归档恢复仍复用原 Issue |
| [034](</home/uisee/zaofu_v7/awesome-zaofu/New_function/Test ZaoFu_Project/04_audit_and_issue_ledger/ZaoFu_issue.md:125>) | 另一合法实例 append 后旧 Index 漏读 | A 以原行/read_all 为准；跨实例新 ID 漏读复用 034 |
| [035](</home/uisee/zaofu_v7/awesome-zaofu/New_function/Test ZaoFu_Project/04_audit_and_issue_ledger/ZaoFu_issue.md:93>) | Index.flush 按时间排序使 warm/cold 查询分歧 | A 不依赖乱序时间或 Index 排序；不得算作同一发现 |
| [038](</home/uisee/zaofu_v7/awesome-zaofu/New_function/Test ZaoFu_Project/04_audit_and_issue_ledger/ZaoFu_issue.md:3>) | 重建后重送 rejection 或丢 checkpoint，业务 retry 重复计费 | B 的跨实例/恢复扩展直接复用 038；已有事实不证明 Provider 重复执行 |

最小退出：A 的同 ID 重送、冲突、新 ID 控制分别有真实账本/报告证据；B 的同实例重送有真实 Store/attempt 证据；schema 例外有独立结果。失败保留实际输入、首次/第二次报告及 Store 快照；若实际违约才记录 Issue，不能从本研究推断直接标“已复现”。

只完成检测器、只看到 Index 一条、只验证 fake projector、或复用 038 却没有 Writer→真实 projector 检查，都不足以把 EVT-05 改为完成。无需扩展到全仓库、真实 Provider、Feishu 网络或 full pytest。
