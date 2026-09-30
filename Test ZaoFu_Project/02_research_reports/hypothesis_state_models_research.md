# Hypothesis 状态模型补测调研

> 文档定位（2026-09-30）：历史调研，正文“当前/建议/尚未实现”仅适用于各段所记时点，不是最新执行队列。现行方案见 [ZaoFu_TestKit.md](../03_plans_and_steps/ZaoFu_TestKit.md)，执行仍按既有暂停/跳过边界；本次不重跑或改写历史结果。
>
> 当前对齐入口：`tests/conformance/test_zaofu_testkit_s11_control_models.py`。控制模型及后续专用模型已存在；早期“缺少两个模型”结论已由后续实现推进。

调研日期：2026-09-14。代码核对基线：`142a62a`，同时读取当前工作树中的 TestKit 和测试；本轮只新增本报告，没有运行测试、修改生产代码、提交或调用真实外部动作。报告按父任务指定位置保存，属于测试建设的研究记录，不新增架构约定。

## 结论与当前缺口

建议为 S11 增加两个小型 `RuleBasedStateMachine`：一个驱动真实 `Orchestrator.run_once()` 的重复唤醒、依赖等待和重开序列；一个驱动真实 `ControlledActionService.execute()` 的提案提交、重复执行、重开、旧 revision 和不匹配请求序列。每一步都把真实 TaskStore 与业务事件计数对照独立的小模型。该建议属于测试：oracle 是可判定的状态和禁止副作用，不含模型排名、LLM judge 或语义能力评分。[TestKit §1、§13](../03_plans_and_steps/ZaoFu_TestKit.md)

当前 [S11 测试](../../../../tests/conformance/test_zaofu_testkit_s11_hypothesis.py) 只有 `EventLogReplayMachine` 和 `test_task_attempt_dispatch_identity_is_idempotent`。前者未显式设置 `TestCase.settings`；后者的 `@settings(max_examples=25, stateful_step_count=20, deadline=None)` 只作用于该 `@given` 函数，`stateful_step_count` 不会限制函数中自行编写的列表循环。不能据此宣称已经实现 Orchestrator/ControlledAction 状态模型，或已经给全部 S11 固定运行预算。版本依据：[pyproject.toml](../../../../pyproject.toml) 声明 `hypothesis>=6.100`，[uv.lock](../../../../uv.lock) 锁定 `6.168.0`。

以下官方资料访问时为 Hypothesis 6.168.0；持久化重放证据应记录实际 Python、Hypothesis 版本与测试源码版本，不能只写“latest”。

## 官方机制及适用方法

| 机制 | 官方约定与本次应用 |
|---|---|
| `@rule` | 声明可组合动作，参数从策略或 Bundle 生成；模型实例同时持有待测系统和辅助状态。将动作拆成真实入口调用，避免一条 rule 隐藏整套流程。 |
| `@initialize` | 每轮普通 rule 之前运行一次；多个初始化方法的顺序不固定，不能依赖相互顺序，也不能有 precondition。单个初始化适合设置有限任务/提案初态。 |
| `@precondition` | 根据实例状态决定动作是否可选，比 rule 内大量 `assume` 更合适。仅限制“已有提案才能 replay”等动作前提；应专门保留 unauthorized/stale 的负向动作。 |
| `@invariant` | 每一步后检查；默认等初始化全部结束后才检查。可用 `check_during_init=True`，前提是该断言在尚未完全初始化时也有效。 |
| `teardown` | 每轮结束清理；官方执行器在 `finally` 调用它。每个例子的资源和补丁必须由该实例清理，构造失败时还要由构造逻辑自身保障清理。 |
| `Bundle` | 可保留已产生的提案/任务 ID 供后续动作复用；本轮的小模型也可只用实例中的有限列表，减少不必要结构。 |

机制依据：[官方 Stateful tests](https://hypothesis.readthedocs.io/en/latest/stateful.html)、[官方 stateful 源码](https://hypothesis.readthedocs.io/en/latest/_modules/hypothesis/stateful.html)。

建议使用单个 `@initialize` 设置所需的参数化初态；临时目录资源可在 `__init__` 分配，每轮实例单独持有。`reopen` 必须仅丢弃生产对象，再从同一个本例临时目录构造新对象；小模型和磁盘数据保留到本轮结束。若把每次重开都变成新建空目录，测试会遗漏持久化恢复行为。全局共享目录、跨例 fixture 状态、按真实时间随机超时均会影响重放；官方将未重置的文件系统/数据库状态列为 flaky 的常见来源。[官方 Flaky failures](https://hypothesis.readthedocs.io/en/latest/tutorial/flaky.html)

## 固定预算与失败重放

建议每个状态机显式设置同一份本地 settings，而非依赖启动环境是否有 `CI`：

```python
STATE_MODEL_SETTINGS = settings(
    max_examples=25,
    stateful_step_count=20,
    derandomize=True,
    database=None,
    deadline=None,
    print_blob=True,
    suppress_health_check=(),
)
TestOrchestrator = OrchestratorMachine.TestCase
TestOrchestrator.settings = STATE_MODEL_SETTINGS
TestControlledAction = ControlledActionMachine.TestCase
TestControlledAction.settings = STATE_MODEL_SETTINGS
```

这是补测建议，不是已执行配置。`max_examples` 控制成功样本探索规模，`stateful_step_count` 限制每个序列长度；丢弃、失败缩减和复核可能增加运行次数，因此 `25 × 20` 不是包含所有阶段的绝对调用上限。`deadline=None` 关闭单例软时限，不能充当墙钟预算；总时限应由现有测试执行器或 CI job 控制，超时记为 `infrastructure_error`。保留默认 shrinking 阶段；不要为了缩短时间把 `Phase.shrink` 删除。[官方 Settings API](https://hypothesis.readthedocs.io/en/latest/reference/api.html#settings)

`derandomize=True` 可在测试、Python 和 Hypothesis 版本稳定时得到相同探索输入，并不消除生产对象中的时钟或外部状态影响。`database=None` 让此固定门禁不依赖本地历史样本；`print_blob=True` 留下临时复现凭据。`@reproduce_failure` 与产生 blob 时的 Hypothesis 版本绑定，不应永久保留为回归 case；`@seed`/`--hypothesis-seed` 会覆盖 derandomize，不能假定普通失败总会打印 seed。[官方 Reproducing inputs](https://hypothesis.readthedocs.io/en/latest/reference/api.html#reproducing-inputs)

失败保存建议：在 teardown 清理之前保留规范化的操作序列、模型状态、相关 EventLog/TaskStore 快照，以及 Python/Hypothesis 版本、测试节点、版本号和输出 blob；若显式传过 seed 再记录 seed。对每次 shrinking 产生的中间失败无限落盘会污染证据，应由测试公共失败收集器限制数量。最小反例确认后，把动作序列写成普通 pytest 固定回归；普通 `@given` 可使用 `@example`。数据库会因版本或代码变更失效，不能替代永久回归。[官方 Replaying failed tests](https://hypothesis.readthedocs.io/en/latest/tutorial/replaying-failures.html)

状态机打印的最小失败程序通常接近可直接执行的 Python。将其变成固定回归时显式调用各动作及对应不变量；直接调用方法不会自动获得 Hypothesis 执行器的 invariant 调度。缩减的目标是提供简化反例，不能据此宣称穷举所有序列或取得完整正确性证明。[官方 Stateful tests](https://hypothesis.readthedocs.io/en/latest/stateful.html)

## Orchestrator：安全动作序列

可复用 [S4 `_orchestrator_context`](../../../../tests/conformance/test_zaofu_testkit_s4.py) 的构造方式：隔离项目目录、真实 EventLog/TaskStore/SessionStore、`backend="mock"`、`TmuxSession(dry_run=True)`。该文件已有 `test_restart_continues_dispatch_without_repeating_side_effect`，可以作为可读基线。生产入口在 [orchestrator.py](../../../../src/zf/runtime/orchestrator.py)，`Orchestrator` 是 `WorkflowRuntimeCoordinator` 的别名。状态模型应覆盖该入口，不以纯 reference model 运算代替 runtime 调用。

最小模型只保留 `task_id -> {status, dependencies, dispatched}` 和期望 dispatch 次数；不要重建真实编排器所有内部缓存。初期任务数限制为 1–3，使用稳定 ID，优先覆盖以下动作：

| 动作 | 生产调用/输入 | 独立 oracle |
|---|---|---|
| 初始化任务 | 经 `TaskStore.add` 放入 backlog 任务与一个未完成依赖 | 初始状态和依赖关系来自测试数据；模型不读取生产派发判定函数 |
| `tick` | 调用当前实例 `run_once()` | 无依赖任务第一次可派发；依赖未满足任务不派发；业务事件计数匹配模型 |
| `duplicate_wakeup` | 不新增完成事实，重复 `run_once()` | 同一活动任务不能新增第二次 dispatch；current dispatch ID 保持 |
| `reopen` | 同 state dir 创建新 Orchestrator，再 `run_once()` | 持久化状态等价；不能因重建对象而再次派发 |
| `stale_retry` | 经 EventWriter 追加旧 `source_dispatch_id` 的 retry/completion-scheduled 事件，再 tick | 当前活动 dispatch ID、状态和当前 attempt 不改变；不能发生新派发 |

每一步读回 TaskStore，并按测试 task ID 过滤真实 `task.dispatched` 事件；同时检查 Event ID 无重复。只比较需要稳定的业务投影，避免把运行时正常新增的观测事件误判为“所有事件必须零增长”。S4 已有 stale scheduled retry 的固定测试，可复用其已验证事件字段；若只调用私有 `_on_completion_scheduled`，必须将证明范围标成 handler，不宣称覆盖 `run_once()` 消费入口。[S4 现有测试](../../../../tests/conformance/test_zaofu_testkit_s4.py)

依赖解除、fanout/fanin、rework、cancel、generation rollover 是 TestKit 要求的后续扩展。初版只实现 dispatch/restart 的模型不能勾选完整 OrchestratorModel；如果加入“完成依赖”，必须通过已有合法事件/受控状态路径及证据，不能让测试伪造缺失 evidence 的成功完成。这一小模型也不证明真实 provider、进程崩溃或并发 race 正确；它们继续由对应测试层承担。[TestKit §1.3、§13](../03_plans_and_steps/ZaoFu_TestKit.md)

## ControlledAction：真实 gate 与 Store 对照

推荐首版选择纯本地 `update-task`，修改一个既有任务的 priority，使用真实 `ControlledActionService.execute()`。它先经过 [ProposalExecutionActionsMixin](../../../../src/zf/runtime/control_actions_proposals.py) 的锁和 [proposal_execution_gate](../../../../src/zf/runtime/kanban_proposals.py)，随后 [_update_task](../../../../src/zf/runtime/control_actions_product.py) 调用 TaskStore 并发出 `task.updated`。对真实业务写入计数，不需要构造一个自身已经去重的 fake sink。

小模型保存：有限提案 ID、每条 lineage 的最新 revision、有效/已解决状态、提案中的预期 priority、当前任务 priority，以及各 lineage 期望的业务写入数。模型使用自己的集合和整数判断；不能调用被测 `proposal_execution_gate()` 再把它的返回当预期结果，也不能读取生产 status 来决定模型下一步状态。

| 动作序列 | 预期结果与禁止结果 |
|---|---|
| `propose(valid payload) -> apply` | 首次精确执行成功；Store priority 改为提案值；该 lineage 只新增一次 `task.updated` 和相应 resolution |
| `apply -> duplicate apply` | 无新 superseding revision 且 payload 一致时返回 `already_resolved`；不新增业务写入 |
| `apply -> discard returned response -> reopen service -> replay` | 保留 EventLog/Store 后重开，丢弃客户端返回值不应让动作重新执行；检查真实 `task.updated` 次数 |
| `propose r1 -> propose r2 -> apply r1` | 同 lineage 的 r1 返回 `proposal_superseded`；priority 与业务写入数不变 |
| `propose -> mutate priority/task_id in execution payload -> apply` | 返回 `proposal_payload_mismatch`；不得改变任务或新增 `task.updated` |
| `unknown proposal_event_id -> apply` | 返回 `proposal_not_found`；Store 不变且业务写入为零 |
| `invalid proposal -> apply` | `valid=False` 返回 `proposal_invalid`；不得写业务状态 |

这些 status 与执行顺序已在 [kanban_proposals.py](../../../../src/zf/runtime/kanban_proposals.py) 核对；同类现有回归见 [test_kanban_pending_proposals.py](../../../../tests/test_kanban_pending_proposals.py)。负向请求可能追加 `runtime.action.failed` 等审计事件，因此“未执行”应断言业务状态及业务事件零增量，不能一概要求 EventLog 完全不变。

有四个边界必须写入测试说明：

1. `execute()` 未携带 `proposal_event_id` 会走受信调用路径；不能把“省略 proposal”当成必然 unauthorized。未知提案、载荷篡改证明的是提案执行约束；错误 token/project scope 必须通过实际 Web/API/CLI 授权入口另测。
2. revision/supersedes 检查先于 `already_resolved`。已执行 r1 后出现 r2，再请求 r1，会先得到 `proposal_superseded`，不是无条件 `already_resolved`。
3. resolved 匹配包含整个 `proposal_id` lineage；同一 proposal_id 的新 revision 不必然是一次新操作。宜使用尚未 resolved 的 lineage 测 supersede，或在模型中明确记录整条 lineage 已解决。
4. [ControlledActionOrchestrator.run](../../../../src/zf/runtime/action_orchestrator.py) 是 attempt/result 记录包装，每次调用仍调用 handler；给它传相同 `operation_id` 不等于已经存在全局 exactly-once gate。不能只测试此包装器或给 spy 增加幂等后声称 Service 正确。

“结果丢失”首版仅指客户端丢弃返回值、持久化 resolution 已完成的情况。业务副作用发生后、resolution 尚未落盘的崩溃窗口，需要独立 failpoint/进程 crash 测试；上述服务重开序列不能证明该窗口安全，也不能直接证明任意远端 operation 的副作用至多一次。[control_actions_proposals.py](../../../../src/zf/runtime/control_actions_proposals.py)、[TestKit §13](../03_plans_and_steps/ZaoFu_TestKit.md)

## 补测完成的可验证标准

- 新状态机绑定明确 settings -> verify：pytest 收集到两个真实实现模型，预算与 derandomize/print_blob 在代码中可核对。
- 随机组合 duplicate/reopen/stale/提案拒绝 -> verify：每条 rule 后都检查 Store 与受测业务事件计数，不是只检查最终状态。
- 精确重放提案 -> verify：多次 service 重建仍无额外 `task.updated`；不以 spy 自身幂等作为依据。
- 可重放失败 -> verify：记录缩减序列、版本与最小状态证据；固定回归能单独运行。
- 覆盖声明准确 -> verify：区分提案 gate 与 token 授权、服务 reopen 与进程 crash、顺序模型与并发 race；未实现 fanin/rework 等仍明确未覆盖。
- 若发现生产缺陷 -> verify：保存最小反例与证据，按测试建设流程记录问题，不在本补测任务内修生产代码。

本轮验证层级是只读源码/测试核对和官方资料调研，未执行 pytest；上述动作表与预算均为实现建议，不是通过结果。
