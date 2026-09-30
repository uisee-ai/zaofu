# ControlledActionModel：结果丢失、生命周期变化与重放判据

> 文档定位（2026-09-30）：历史调研，正文“当前/建议/尚未实现”仅适用于各段所记时点，不是最新执行队列。现行方案见 [ZaoFu_TestKit.md](../03_plans_and_steps/ZaoFu_TestKit.md)，执行仍按既有暂停/跳过边界；本次不重跑或改写历史结果。
>
> 当前对齐入口：`tests/conformance/test_zaofu_testkit_s11_action_loss_model.py`。已有结果丢失模型入口；动作族覆盖与任意远端 exactly-once 分开，不外推缺失组合。

2026-09-14。源码 HEAD 为 `142a62a`；研究对象包含当前工作树源码和现有测试。本轮按 research 技能只做第一方源码调查，仅新增本文件；没有运行测试、导入项目模块、启动运行态、访问 Provider/远端、登记 Issue 或修改测试。下述“当前行为”是源码结论，“现有实测”只引用已记录实验，均不代表本轮复跑通过。

成功标准：定位真实提交点 → 明确独立模型可观测状态 → 区分客户端响应丢失和持久化缺口 → 给出可执行测试闭包。原要求是 request、approve、apply、result 丢失、replay，检查未授权不执行及 operation 副作用至多一次：[TestKit §13](../03_plans_and_steps/ZaoFu_TestKit.md#13-测试域九hypothesis-性质和状态机测试)。

## 1. 结论与真实提交顺序

可以在现有 S11 上补充一个有界的真实 `ControlledActionService.execute()` 模型，参数化 nested `operator.action.proposed` 和 legacy `kanban.agent.action.proposed`。正常生成轨迹覆盖提案、批准调用、重开、响应丢失、过期、dismiss 和换代；两个已知“更新已提交而 resolution 尚未写入”窗口宜单独运行生成前缀加固定故障恢复探针，不给整个状态机加 xfail。[当前 S11](../../../../tests/conformance/test_zaofu_testkit_s11_control_models.py)、[真实 proposal 包装](../../../../src/zf/runtime/control_actions_proposals.py)。

`ControlledActionService` 的 `execute()` 来自 `ProposalExecutionActionsMixin`。只要 payload 带非空 `proposal_event_id`，就先持有 `<state_dir>/locks/kanban-proposal-execution`，读取 ledger 后执行 `proposal_execution_gate()`。已解决则直接返回 `_already_resolved_result()`；否则走 `_run_controlled_action()` → `ControlledActionOrchestrator.run()` → `ControlledActionService._execute_action()` → `ProductActionsMixin._update_task()`。[入口](/home/uisee/zaofu_v7/src/zf/runtime/control_actions_proposals.py:20)、[分派](/home/uisee/zaofu_v7/src/zf/runtime/control_actions.py:168)。

真实 update-task 成功顺序为：

1. caller 写请求事件；proposal gate 通过。
2. `ControlledActionOrchestrator._started()` 写 `runtime.action.attempt.started`。
3. `_update_task()` 调真实 `TaskStore.update()`，非终态 Task 经 `_save_raw()` 保存。
4. `_update_task()` 写 `task.updated`；`ActionEmitMixin._completed()` 写 `runtime.action.completed` 与 `<surface>.action.completed`。
5. handler 返回；`ControlledActionOrchestrator._completed()` 写 `runtime.action.attempt.completed`，随后构造 `action_result` envelope。
6. `_run_controlled_action()` 写对应 proposal resolution，随后 `execute()` 返回。

来源：[attempt 包装](/home/uisee/zaofu_v7/src/zf/runtime/action_orchestrator.py:46)、[真实更新](/home/uisee/zaofu_v7/src/zf/runtime/control_actions_product.py:631)、[Store 保存](/home/uisee/zaofu_v7/src/zf/core/task/store.py:292)、[completion 事件](/home/uisee/zaofu_v7/src/zf/runtime/control_actions_emit.py:8)、[resolution](/home/uisee/zaofu_v7/src/zf/runtime/control_actions_proposals.py:125)。

现有 S4 已使用以下四个命名点，子进程入口实际是测试文件内 `_child()`；当前 `tests/testkit/fixtures/` 下没有 `action_commit_child.py`，不要按假定文件名接线。

| 命名点 | 实际测试 seam | 中断后本次更新数 / task.updated / attempt.completed / resolution |
|---|---|---|
| `before_store_update` | 包装 `TaskStore.update()`，调用原方法前 | 0 / 0 / 0 / 0 |
| `after_store_update_before_task_updated` | `FaultWriter.emit('task.updated')` 调原方法前；Store observer 已记账 | 1 / 0 / 0 / 0 |
| `after_action_result_before_resolution` | 对应 resolution 的 `emit()` 调原方法前 | 1 / 1 / 1 / 0 |
| `after_resolution` | 对应 resolution 的真实 `emit()` 返回后 | 1 / 1 / 1 / 1 |

来源：[S4 `_child` 与 POINTS](/home/uisee/zaofu_v7/tests/conformance/test_zaofu_testkit_s4_action_crash.py:44)。这里的 result 是 runtime action/attempt 结果边界，不是客户端已经收到 HTTP 响应。现有 S4 用 `os._exit(86)`，验证 witness、PID、窗口快照、新进程恢复及独立 30 秒 timeout。[恢复探针](/home/uisee/zaofu_v7/tests/conformance/test_zaofu_testkit_s4_action_crash.py:196)。

建议的同进程生成模型可以在相同 seam 抛专用 `BaseException`，避免 `ControlledActionOrchestrator.run()` 的 `except Exception` 额外写 attempt.failed 后再恢复；必须在精确命名点核实 witness，并在测试入口显式捕获该专用类型。它会正常展开 context manager/释放锁，只证明中断与对象重建组合；不能替代 S4 的硬退出、OS 锁恢复或任意时刻崩溃证明。[异常边界](/home/uisee/zaofu_v7/src/zf/runtime/action_orchestrator.py:63)。这是建议的测试能力，本轮未实现。

## 2. 新旧 proposal、批准与 HTTP 幂等的边界

新旧 nested proposal 经过完全相同的 gate 和执行锁；差别是执行/撤销时写 `operator.action.resolved` 或 `kanban.agent.proposal.resolved`。gate 同时承认两类 resolution，并按 `proposal_event_id` **或** `proposal_id` 判重，不根据 `task.updated`、`runtime.action.attempt.completed` 判重。[常量](/home/uisee/zaofu_v7/src/zf/runtime/kanban_proposals.py:17)、[匹配条件](/home/uisee/zaofu_v7/src/zf/runtime/kanban_proposals.py:310)、[类型选择](/home/uisee/zaofu_v7/src/zf/runtime/control_actions_proposals.py:130)。

这里没有独立的“approve 事件必须先存在”这一通用 gate：Web Accept / 飞书批准按钮在受信任入口决定执行已提出的语义 payload，并调用 `execute()`；未带 proposal ID 的服务调用走普通 action，不会凭空要求提案审批。不要用“没有 model.approved 标志就拒绝调用 execute”的测试替身来证明产品授权，也不要把 `actor='unauthorized'` 当作真实 token 校验。[服务入口](/home/uisee/zaofu_v7/src/zf/runtime/control_actions_proposals.py:28)、[Web token gate](/home/uisee/zaofu_v7/src/zf/web/server.py:5678)、[飞书批准 caller](/home/uisee/zaofu_v7/src/zf/cli/feishu.py:2436)。

`operator-intent-approve` + `execute_proposals` 是另一协议：消费顶层 `intent_id`、`proposal_id`、`proposals[]`，直接 `_execute_action()` 执行链，并用 `operator.action.executed` 判重。不能因为事件名也叫 `operator.action.proposed` 就把它当成 nested proposal approve/replay；本模型不覆盖该链。[`_operator_intent_lifecycle` / `_execute_intent_proposals`](/home/uisee/zaofu_v7/src/zf/runtime/control_actions_product.py:1212)。

HTTP idempotency 是另一层：`_web_action()` 验证 token 后，`_reserve_idempotency_key()` 按 key+action+payload_hash 返回 replayed/pending/conflict，命中 replay 时可能根本不进入 service。成功调用 service 后 `_complete_idempotency_key()` 保存响应。因此 API 同 key 再调用可能返回原完整响应，而 service 相同 proposal 再调用返回的是 `already_resolved` 摘要，两种 result oracle 不应混用。服务器重开 reconciliation 把 pending key 变成 `interrupted_by_server_restart` 失败响应，也不是恢复执行。[Web reserve](/home/uisee/zaofu_v7/src/zf/web/server.py:5761)、[update caller](/home/uisee/zaofu_v7/src/zf/web/server.py:6415)、[key store 与 reconciliation](/home/uisee/zaofu_v7/src/zf/web/projections/request_util.py:29)。

`actions_pending.py::PendingActionsStore` 管的是 worker.reply/respawn/drain 请求，`RESULT_TYPES` 也不是这里的 action result；不能拿其 rebuild 充当 proposal 丢结果恢复。[队列类型边界](/home/uisee/zaofu_v7/src/zf/runtime/actions_pending.py:32)。

### nested operator proposal 的有效夹具形状

本模型最小可执行字段为事件 `id/type` 与 `payload.proposal = {action:'update-task', valid:True, payload:{task_id, priority}}`。为了检查身份和投影，建议显式提供以下形状，而不依赖兼容默认值：

```text
ZfEvent(id=唯一请求事件ID, type=operator.action.proposed,
        actor=受信任提案发布方, task_id=目标Task, correlation_id=测试run,
        payload={schema_version: operator.action.proposal.v1,
                 conversation_id, project_id, thread_key, turn_id,
                 proposal: {proposal_id, proposal_digest, revision,
                            expires_at, supersedes,
                            action: update-task, requested_action: update-task,
                            payload: {task_id, priority},
                            valid: true, validation_error: ""}})
```

生产 `WorkflowStartService` 使用顶层 `operator.action.proposal.v1` 和此 nested 结构；飞书通用提案发布使用同 nested 结构但没有 schema_version，故 schema_version 不是此 gate 的通用硬必填。[workflow-start producer](/home/uisee/zaofu_v7/src/zf/runtime/workflow_start.py:622)、[飞书 producer](/home/uisee/zaofu_v7/src/zf/integrations/feishu/agent_conversation.py:635)。标准 extractor 还输出 reason/confidence/mutates_task_state，并计算语义 digest；gate 重新计算 proposed/execution payload digest，而不会验证任意给定 `proposal_digest` 是否等于计算值。[extractor](/home/uisee/zaofu_v7/src/zf/web/proposal_extraction.py:497)、[gate digest 对比](/home/uisee/zaofu_v7/src/zf/runtime/kanban_proposals.py:289)。

默认 `EventWriter(EventLog(...))` 没有启用 schema registry；“真实 Writer 写入成功”不能声称严格 payload schema 已通过。配置 registry 来源为 `zf.yaml workflow.dag.event_schemas`，未知类型在 registry 中为 loose。本研究未发现可替代上述消费者字段契约的 operator 专属强制 payload schema。[Writer 默认值](/home/uisee/zaofu_v7/src/zf/core/events/writer.py:62)、[registry 规则](/home/uisee/zaofu_v7/src/zf/core/verification/event_schema.py:1)。

## 3. 独立 reference model 与生成动作

现有 `ControlledProposalMachine` 保存 proposals/latest/applied/expected_priority，驱动真实 update-task；已覆盖 unknown proposal、payload tamper、pending revision supersede 和 reopen，但没有显式 request/approve/result-loss 阶段，也没有 expiry/dismiss。它用业务事件数量检测重复，尚未像 S4 那样独立观测“Store 已写但 task.updated 未写”的窗口。[现有模型](/home/uisee/zaofu_v7/tests/conformance/test_zaofu_testkit_s11_control_models.py:122)。

建议模型保存自己发出的事实：每个提案的 family/revision/语义字段/有效期、latest revision、显式 supersedes、lineage resolution（executed/dismissed）、每 operation 的预期成功提交次数、预期 task.updated 数、预期 Task 值、未收到客户端结果的请求、命名中断点。模型按命令及明确中断点推演预期，不调用 `proposal_execution_gate()` 或 `pending_kanban_proposals()` 作为参考模型；这两个应是被测结果。也不从 service 返回值“学出”应该允许多少副作用。

observer 包装 `TaskStore.update()`，每次都先调用原函数，成功返回后无条件记录一次；不缓存结果、不读取过去回执决定跳过、不进行 key 去重。它是实际观测，不能驱动模型增加许可次数。最终 Task 值、业务事件与 observer 三者都需比对，因为同值重复赋值会再次 `_save_raw()`，且第一次更新可能没有业务事件。[Store 行为](/home/uisee/zaofu_v7/src/zf/core/task/store.py:313)、[现有 S4 observer](/home/uisee/zaofu_v7/tests/conformance/test_zaofu_testkit_s4_action_crash.py:87)。`ExternalEffectSpy.execute()` 内部自带 `_results` 去重，不能作为这项 SUT 一次执行证明的 effect 实现。[spy](/home/uisee/zaofu_v7/tests/testkit/external_sinks.py:36)。

建议拆分以下动作；表中均为待补测试设计：

| 动作 | 真实操作 | 独立预期 |
|---|---|---|
| propose/request | Writer 发布 nested proposal；caller 发布请求事件 | 不更新 Task，不产生业务 resolution |
| approve/apply | 从生成的批准请求调用真实 execute | 当前有效、语义一致、未解决时一次更新；其他情况零新增 |
| lose_client_reply | execute 完成后丢弃返回对象，保持完整持久化状态 | 重开重放不新增更新；可收到 already_resolved 摘要 |
| interrupt_before_store | 命名点中断；重开原目录 | 若仍有效且未撤销/换代，恢复累计恰好一次更新 |
| interrupt_after_resolution | resolution 真实写入后中断；重开 | 已提交一次，恢复不再增长 |
| expire | 控制时间越过预先写入的 expires_at | 之后 apply/dismiss 被拒绝；不改旧 Task，不回滚历史 |
| dismiss | execute(kanban-proposal-dismiss) | 尚有效且未解决时一个 dismissed resolution、零新增 Task 更新 |
| supersede | Writer 发布更高 revision，或新 proposal_id+supersedes | 旧事件执行被拒绝；新操作的许可独立建模 |
| tamper/unknown/action mismatch/invalid | 对真实 service 发出无效请求 | gate fail-closed；无 Task 更新，无新 resolution |
| reopen/replay | 新 Log/Writer/Store/Service，对原身份再调用 | 状态从同目录恢复，禁止重建 fixture 或清空 ledger |

request 和 approve 可以作为模型输入命令分别记录，真正的批准落地仍以现有受信任 caller 的 execute 为准。要证明“未授权不执行”，另外调用真实 Web `_web_action()`/TestClient 测试缺失或错误 token，断言不进入真实 store 更新；不能只检查 proposal_not_found。已有服务模型文件头也明确区分 proposal gate 与 HTTP token 授权。[S11 注释](/home/uisee/zaofu_v7/tests/conformance/test_zaofu_testkit_s11_control_models.py:123)、[Web 拒绝](/home/uisee/zaofu_v7/src/zf/web/server.py:5690)。

每轨迹建议最多 2–4 个提案、两个 revision、20 步；保持现有 25 examples/derandomize/database=None，绑定外层运行预算和 `ModelEvidence`。固定用例必须强制走实际 approve → 完成后丢结果 → reopen → replay，不能只依赖 Hypothesis 恰好选中。[现有设置](/home/uisee/zaofu_v7/tests/conformance/test_zaofu_testkit_s11_control_models.py:28)、[证据支持](../../../../tests/testkit/model_evidence.py)。

## 4. expiry/dismiss/supersede 夹在中断与恢复之间

gate 的当前优先顺序是：source 存在 → action 匹配 → valid → expiry → supersedes/较新 revision → semantic payload 相同 → 已 resolution。dismiss 跳过 action/valid/semantic payload 检查，但仍受 expiry 和 supersede 约束。[完整 gate](/home/uisee/zaofu_v7/src/zf/runtime/kanban_proposals.py:219)。因此应保留以下确定判据：

| 前情与后续请求 | service 当前合理 oracle | 不应错误要求 |
|---|---|---|
| 正常 execute 已完成 → expiry → 原请求 replay | proposal_expired；历史 effect=1，零新增 | 必须 always already_resolved |
| 正常 execute 已完成 → 同族更高 revision → 旧请求 replay | proposal_superseded；零新增 | 因旧请求曾成功就绕过 currentness |
| 已解决 → changed payload replay | proposal_payload_mismatch（若此前更高优先级检查均通过） | 先返回旧成功再忽略篡改 |
| 未提交中断 → dismiss → replay | dismissed resolution 一次，Task 保持初值；有效请求 replay 为 already_resolved | replay 又真正 apply |
| 已提交未 resolution 中断 → dismiss → replay | Task 保留已提交值；累计 observer=1；dismissed resolution 一次；replay 零新增 | 撤销自动回滚或历史 effect=0 |
| 已提交未 resolution 中断 → expiry → replay | proposal_expired，零新增；没有自动补 resolution/task.updated | 因当前 gate 阻断就把恢复闭包判为完整 |
| 已提交未 resolution 中断 → supersede → 旧 replay | proposal_superseded；旧效果保留且零新增 | 用旧操作的最终值覆盖新一代结果 |
| 已完成/已 dismiss 的同族 proposal_id → 新 revision apply | 若最新且未过期且 payload 一致，仍 already_resolved | 新 revision 天然具有新执行资格 |
| 已完成 → 新 proposal_id（可带 supersedes）→ 新提案 apply | 新操作可有一次作用；旧历史保留 | 整个 Task/所有提案全程最多一条更新 |

以上“不回滚”直接来自 `_kanban_proposal_dismiss()` 只写 resolution/completion、没有 TaskStore 更新；执行包装对 create-task/dismiss 不追加第二个通用 resolution。[dismiss handler](/home/uisee/zaofu_v7/src/zf/runtime/control_actions_product.py:360)、[包装排除项](/home/uisee/zaofu_v7/src/zf/runtime/control_actions_proposals.py:125)。

同 proposal_id 的 resolution 在 gate 和 pending projection 都是族级屏障，包括 dismissed。生产 workflow-start 在 resolved 后再次批准会生成带 `-rN` 后缀的新 proposal_id，代码注释明确是为了不让旧 resolution 压住新批准。[production remint](/home/uisee/zaofu_v7/src/zf/runtime/workflow_start.py:594)、[pending resolution fold](/home/uisee/zaofu_v7/src/zf/runtime/kanban_proposals.py:169)。

部分提交后、尚无 resolution 的同族更高 revision 是更细的组合：旧 revision 可能已有一个 effect，新 revision 也可能执行新语义。因此 operation 不能简单等同于整个 proposal family；建议按 proposal_id+revision+语义定义 operation，再单独保存 family terminal 状态。同一 revision 在两个 surface 重新发布的等价事件不能各给一次执行许可。若先做最小实现，可让“中断后换代”只验证旧 replay 不增长，另用新 proposal_id 测新动作；必须显式保留同族部分提交后换代执行这一范围，不能隐藏成无条件通过。

expiry 应通过注入时钟（限 `kanban_proposals` 模块的 datetime seam）越过预先发布的截止时间，不改旧 ledger 的 expires_at。解析失败被当前 `_expired()` 视为过期，无时区时间按 UTC，边界是 `expires_at <= now`；可加一个固定截止时刻等于 now 的用例。[`_expired`](/home/uisee/zaofu_v7/src/zf/runtime/kanban_proposals.py:359)。同一时钟也驱动 pending projection，避免测试生成器和被测查询各用不同墙钟。

pending projection 不检查业务更新是否已经发生；它只基于 proposals、resolution、task.created 等 fold，另过滤 expiry/supersedes。所以“中断后更新成功但 resolution 缺失 → 提案过期/换代后从 pending 消失”是可解释的投影结果，但没有证明原操作的缺失 ledger 已恢复；严格的恢复完整性要求若要加入，必须单独建探针、保留原始证据再判断是否新缺陷。[pending projection](/home/uisee/zaofu_v7/src/zf/runtime/kanban_proposals.py:102)。

result replay 的合理对比不是返回 dict 全相等。service `already_resolved` 返回 status、proposal/task 身份、resolution event ID；不返回原 action_result envelope，`event_id` 也从原 task.updated ID 变成 resolution ID。应检查 resolution 引用对应已持久化事件、proposal identity/revision/task 正确、没有新增业务作用。若新增“必须原样重放原响应”的要求，那是当前 service 尚未实现的独立能力。[`_already_resolved_result`](/home/uisee/zaofu_v7/src/zf/runtime/control_actions_proposals.py:167)。

## 5. 正负控制、xfail 与必须联调的调用者

建议正控制为：两种 proposal 类型各执行一次 priority 3→1，检查 observer/task.updated/resolution 都恰好一次；完成后丢返回值，重开再重放两次仍无增长。命名中断要求匹配目标 state/task/proposal、命中 witness、准确窗口 snapshot；检测不到中断或出现其他异常应普通失败。固定 expiry-after-completion、dismiss-after-partial、supersede-before-replay 序列用于验证上述优先级。

建议负控制至少两种：第一，在已通过状态检查的轨迹中追加额外真实 `task.updated`，oracle 必须失败；现有 S11 已有这一对照。第二，额外调用一次真实 `TaskStore.update()` 给相同值、不添加业务事件，observer oracle 必须失败，证明它能发现仅 final value/event count 看不见的重复。专用负控制应隔离证据目录，避免把人为损坏样本误登记为生产缺陷。[现有负控制](/home/uisee/zaofu_v7/tests/conformance/test_zaofu_testkit_s11_control_models.py:297)。

已知故障探针应先核实窗口和恢复可执行，再对真实重复提交抛 `DuplicateActionEffect`；仅该异常允许 `strict=True, raises=DuplicateActionEffect`。前缀生成、授权失败、窗口 witness、payload mismatch、EventWriter 阻断、超时、模型断言失败均不能被 xfail 吞掉；不要给正常状态机整体 xfail。[已有 narrow xfail](/home/uisee/zaofu_v7/tests/conformance/test_zaofu_testkit_s4_action_crash.py:39)、[判重异常](/home/uisee/zaofu_v7/tests/conformance/test_zaofu_testkit_s4_action_crash.py:166)。

仅新增测试时的必要影响闭包建议如下；这些是后续运行清单，本轮均未运行：

| 目标 | 必跑现有入口/测试 |
|---|---|
| service proposal gate、dismiss、过期、revision、跨 surface | `tests/test_kanban_pending_proposals.py`；现有 S11 control models；现有 S4 action crash；新增模型/固定探针 |
| 真实 action result/attempt envelope | `tests/test_action_orchestrator.py` |
| TaskStore/Writer 共享契约 | `tests/test_task_store.py`、`tests/test_event_writer_schema.py`；若涉及 schema fixture，再加 `tests/test_event_schema.py` |
| Web 真实 update caller、token、HTTP idempotency | `tests/test_web_kanban_upgrade.py::test_create_and_update_task_priority_through_web_action`；`tests/test_web_server.py::TestApiWebActions`（含 `test_web_action_idempotency_replays_same_request`） |
| Web 重开 pending key 语义 | 若声明 HTTP 重启结果保证，跑 `tests/test_web_headless_recovery.py` |
| 飞书批准直接 caller（mock transport） | `tests/test_feishu_channel_kanban_e2e.py::test_kanban_proposal_card_executes_once_through_signed_callback`、`test_kanban_proposal_card_refreshes_signed_target_on_revision` |
| nested operator proposal 生成者/可见投影 | 若修改这些边界，补 `tests/test_workflow_start_service.py`、`tests/test_event_read_model.py`，不因仅参数化事件类型扩张到所有 workflow 测试 |
| TestKit 注册、证据与已知失败分类 | `tests/conformance/test_zaofu_testkit_s0.py`、`tests/testkit/test_model_evidence.py`，以及 TestKit runner 中对应 stage 的整段运行 |

调用链证据：[Web update caller](/home/uisee/zaofu_v7/src/zf/web/server.py:6415)、[飞书 caller](/home/uisee/zaofu_v7/src/zf/cli/feishu.py:2508)、[Web 测试类](/home/uisee/zaofu_v7/tests/test_web_server.py:2639)、[飞书可读回归](/home/uisee/zaofu_v7/tests/test_feishu_channel_kanban_e2e.py:129)。严格 xfail 即使 pytest exit=0，也不能把产品 verdict 计为 passed；已有 032 登记保留了外层 verdict=fail。[032 结果记录](../04_audit_and_issue_ledger/ZaoFu_issue.md#zf-test-032controlledaction-提交后proposal-resolution-写入前崩溃会重复更新)。

## 6. ZF-TEST-032 覆盖什么，还没有证明什么

032 已记录真实 legacy update-task 在两个中间窗口恢复后成功 Store 调用变成 2 次；after_store 点 task.updated 只有 1 条，after_action_result 点有 2 条。两者最后 Task 值都正确且 resolution 只有一条，说明只看最终值或卡片消失会漏检。现有 S4 的 POINTS/seed 都固定 legacy 类型，本轮没有执行该实验。[已登记实验](</home/uisee/zaofu_v7/awesome-zaofu/New_function/Test ZaoFu_Project/04_audit_and_issue_ledger/ZaoFu_issue.md:194>)、[当前 seed](/home/uisee/zaofu_v7/tests/conformance/test_zaofu_testkit_s4_action_crash.py:52)。

新 nested operator update-task 的控制路径仅 resolution 类型不同，源码支持“可能复现相同根因”的判断；但 **当前 032 的实测不能自动覆盖新事件分支**。必须先让新参数裸跑（或明确 runxfail）、保存真实重复提交证据后，才能把该具体变体映射到 032。不要先用 032 吞掉一个尚未复现的新分支错误，也不要因是同类窗口就另登记重复 Issue。

以下能力仍是未证明或待实现范围：同族部分提交后换代并执行新 revision 的所有组合；部分提交被过期/撤销后的完整账本 reconciliation；service 原响应持久化重放；operator-intent proposal chain；任意 action 的外部副作用至多一次；Provider、GitHub、飞书远端 sink；断电持久性。新模型若完成本文有界动作，也只证明这些动作空间下的本地更新、准入和结果重放，不能宣称整个 §13 或远端 exactly-once 已完成。
