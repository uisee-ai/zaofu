# TaskAttempt generation rollover 有界调研

> 文档定位（2026-09-30）：历史调研，正文“当前/建议/尚未实现”仅适用于各段所记时点，不是最新执行队列。现行方案见 [ZaoFu_TestKit.md](../03_plans_and_steps/ZaoFu_TestKit.md)，执行仍按既有暂停/跳过边界；本次不重跑或改写历史结果。
>
> 当前对齐入口：`tests/conformance/test_zaofu_testkit_s11_generation_model.py`。已有 generation 模型；不再把“仅 attempt 列表性质”作为当前全部实现。

调研日期：2026-09-14；HEAD：`142a62a`。按父任务要求只读源码、测试和现有问题记录，只新增本文件；未运行测试、Provider、容器、远端操作，未改生产代码。采用 research 技能的一手源码核对方法；本任务已经是后台研究子任务，按明确范围不再委派。Hypothesis 通用机制沿用 [既有官方资料调研](hypothesis_state_models_research.md)，本次无新增外部事实。

## 结论

§13 的 generation rollover 缺口确实存在：当前 [TaskAttemptLifecycleModel](../../../../tests/conformance/test_zaofu_testkit_s11_attempt_model.py) 只生成 dispatch、timeout、retry、late result 和成功后 series 重置；`result()` 直接调用 validate/settle helper。文件头已明确不覆盖 workflow/generation admission。补测宜增加一个小型“task-map/合同换代 + caller admission”模型，保留原 transport/预算模型，不把几种不同 generation 合成一个计数器。[TestKit §13](../03_plans_and_steps/ZaoFu_TestKit.md)

最小可行真实换代链是 `ingest_task_map_to_kanban()` → `commit_task_map_materialization()` → `TaskContractAuthorityService.replace()` → `write_task_doc()` → `TaskStore.patch_contract_fields()`。相同 `task_id` 在 `add_many()` 返回 skipped 后，物化循环仍以新合同执行 replace，因此可对同一 Task 摄入新 `task_map_ref` 和 `source_refs.task_map_generation`，保留活动 attempt 来单独证明 revision admission。换代结果必须读回 Store 和 materialization/contract 更新事件，不直接改 JSON。[product_delivery.py](../../../../src/zf/runtime/product_delivery.py:48)、[task_map_materialization.py](../../../../src/zf/runtime/task_map_materialization.py:77)

## 四类身份必须分开

| 身份 | 真实约定 | 模型应如何表述 |
|---|---|---|
| `task_map_generation` | 优先读取 `contract.evidence_contract.task_map_generation`，其次其中 `source_refs.task_map_generation`；缺少显式值时从 task-map/source ref 的 SHA-256 派生。 | 不透明字符串。模型自有换代序号只能表示动作先后，不能对生产 generation 字符串做数值大小比较。 |
| `contract_revision` | `compute_task_capsule_revisions()` 根据 canonical 合同内容派生；source/capsule revision 有各自输入。 | 保存每代摄入后冻结的身份快照，断言换代改变预期字段，不能伪造 `revision += 1`。 |
| fanout generation | `fanout.started` 的实例及 logical_key 投影决定当前同类实例，后续等价实例将旧实例标记 superseded。未知 legacy fanout 默认 current。 | 单独模型和真实 fanout 生命周期；给普通结果添 `generation=2` 并不能制造 fanout 换代。 |
| `TaskAttempt.series` / `ordinal` | 同 attempt lane 最新记录 succeeded 后才 `series + 1, ordinal = 1`；否则同 series 的 ordinal 递增并受预算约束。 | 合同换代本身不重置预算、不保证新 series、不自动生成 attempt。 |

来源：[task_contract_snapshot.py::task_map_generation](../../../../src/zf/runtime/task_contract_snapshot.py:180)、[task_doc.py](../../../../src/zf/runtime/task_doc.py:49)、[fanout_identity.py](../../../../src/zf/runtime/fanout_identity.py:95)、[task_attempts.py::ensure_for_dispatch](../../../../src/zf/core/state/task_attempts.py:120)。另有 Task Pipeline `generation_id`/`task_map_generation` 与 operation retry generation；[S3 身份矩阵](../../../../tests/conformance/test_zaofu_testkit_s3_generation.py) 已明确 operation generation 改变不等于获准 task-map generation 改变，本次不扩大到完整 Task Pipeline。

TaskAttempt 的 lane 还受 identity_version 影响：role-v1 与 operation-v2 的 attempt_key 不同；本轮沿用现有非 Task Pipeline dev lane即可。不能把一条 lane 的预算结论外推到所有 role/operation。[task_attempts.py](../../../../src/zf/core/state/task_attempts.py)

## 真正 caller admission 的顺序

`Orchestrator._react_to_events()` 先 hydrate result，再 `_reject_invalid_lifecycle_event()`，拒绝即 continue；通过后才 housekeeping 和 `_settle_task_attempt_result()`。因此要用 `_react_to_events([已由 EventWriter 追加的事件])` 覆盖真实事件消费者，或以 `run_once(events=[...])` 覆盖再上一层 tick；直接调用 validate/settle 无法证明 generation 拒绝。[orchestrator.py](../../../../src/zf/runtime/orchestrator.py:2416)

普通、带 task_id 的 worker completion 路径依次包含 Run admission、TaskAttempt admission、fanout terminal admission、dispatch token/actor/revision/evidence admission。`validate_task_attempt_result()` 只检查 workflow_run_id、operation_id、attempt_id、lease_id、dispatch_id、active 状态和 lease；没有 task_map_generation 字段。`settle_task_attempt_result()` 的前置约定就是已经通过普通生命周期准入，也不应独立承担完整 generation gate。[runtime_authority_bridge.py](../../../../src/zf/runtime/runtime_authority_bridge.py:110)、[orchestrator_reactor.py](../../../../src/zf/runtime/orchestrator_reactor.py:2504)、[task_attempt_runtime.py](../../../../src/zf/runtime/task_attempt_runtime.py:301)

旧合同拒绝由 `_reject_stale_task_capsule_revision()` 完成：对 dev.build.done 等完成事件比较 Store 的非空 source_revision、contract_revision、capsule_revision，发出 `task.completion.stale_rejected` 和相应拒绝审计。这证明“由 task-map 换代引起的合同身份变化”被阻止，不等于直接验证结果 payload 的 task_map_generation。[orchestrator_reactor.py](../../../../src/zf/runtime/orchestrator_reactor.py:2682)

必须显式配置 `ContractDConfig(dispatch_token_required=True)`；关闭该设置、trusted actor、fanout child 分支均可能提前返回，未走该普通 capsule gate。结果使用 `actor="dev", origin="worker"`，role 明确 publishes dev.build.done，payload 保留原 dispatch/attempt 身份；不要用 zf-cli 或 trusted kernel origin。[同上 admission](../../../../src/zf/runtime/orchestrator_reactor.py:2504)、[既有 capsule 测试 fixture](../../../../tests/test_task_capsule_revision_gate.py:55)

## 隔离 fixture 与动作建议

1. 每个状态机实例独立 TemporaryDirectory，显式 `project.state_dir="state"`；创建真实 SessionStore、TaskStore、EventWriter、TaskAttemptStore 和 Orchestrator，传 `project_root`。使用 recording transport，不启 Provider/宿主进程，沿用 fake clock。[现有模型](../../../../tests/conformance/test_zaofu_testkit_s11_attempt_model.py)
2. 摄入最小 task-map.v1：单一 TASK-1/dev、scope、可执行形式 verification、verification_tiers、acceptance；refs 绑定 `workflow_run_id="RUN-1"` 与每代独立 task-map ref/generation。workflow_run_id 需在合同 evidence 的实际 run binding 位置保持稳定，不能只靠 correlation_id 猜测。[product_delivery 合同构造](../../../../src/zf/runtime/product_delivery.py:800)、[现有摄入 fixture](../../../../tests/test_product_delivery.py:36)
3. 换代使用摄入函数及真实 writer，检查 `result.passed`、没有 task_doc_failures、Store 当前 generation、三类 revision 和 authority revision。对同一 task 更新不要加 `supersedes_task_map_ref`：该入口会取消匹配旧 task、清空 active_dispatch_id，这是另一种合法替换轨迹。[product_delivery.py](../../../../src/zf/runtime/product_delivery.py:1001)、[已有取消/替换回归](../../../../tests/test_product_delivery.py:1303)
4. 初次 `_send()` 经真实 `_send_transport_task` 得到 attempt；将该时点的五元身份和 revision/generation 冻结进历史结果。换代动作不调用 `_send()`，先保留活动旧 attempt，以免旧结果只因 attempt_id 不匹配就被更早拒绝。[scheduler `_send`](../../../../tests/test_scheduler_task_attempt.py:123)
5. 每个结果先 EventWriter.append，再交给真实 `_react_to_events([event])`；模型仅根据自己的历史 generation、活动 attempt、lease 时钟预测接受。若选择 run_once，必须控制其自动 dispatch/reconciliation 对模型的影响，且不把无关审计计为业务推进。[orchestrator.py](../../../../src/zf/runtime/orchestrator.py:2416)
6. `reopen` 只重建 runtime/store 对象，复用该实例的目录、事件与参考状态。保持原 ModelEvidence 装饰器和 teardown 清理顺序；本次应另存 generation、revision 历史、当前 attempt、caller 决策及拒绝审计来源。[原模型](../../../../tests/conformance/test_zaofu_testkit_s11_attempt_model.py)

按当前 schema/摄入源码整理的最小输入如下（未执行验证）；每次只替换 `generation` 与 `task_map_ref`，固定 task_id 和 run_id。verification 是合同声明，此摄入函数不会执行该命令。

```python
task_map = {
    "schema_version": "task-map.v1", "feature_id": "F-ATTEMPT",
    "source_refs": {"task_map_generation": generation},
    "tasks": [{
        "task_id": "TASK-1", "title": "attempt generation model",
        "owner_role": "dev", "scope": ["src/example.py"],
        "verification": "python -c 'pass'", "verification_tiers": ["runtime"],
        "acceptance": ["current generation is admitted"],
        "evidence_contract": {"workflow_run_id": "RUN-1"},
    }],
}
result = ingest_task_map_to_kanban(
    state, task_map, task_map_ref=f"artifacts/maps/{generation}.json",
    writer=runtime.event_writer, correlation_id="RUN-1",
)
```

TaskStore 新建任务时从 evidence 兼容读取 run binding，后续 Authority.replace 保留 current execution binding；实际测试仍应断言 `task_workflow_run_id(task) == "RUN-1"`，避免错误 Run 绑定让旧结果更早被拒绝而掩盖本次目标。[task_contract_authority.py](../../../../src/zf/runtime/task_contract_authority.py:49)

## 必须保留的固定轨迹

| 轨迹 | 独立 oracle 与禁止结果 |
|---|---|
| ingest G1 → dispatch A1 → ingest G2 → old result(G1,A1) | A1 仍是当前、lease 未到期，排除更早 attempt 拒绝；期望 capsule stale block，A1 不 succeeded，terminal_event_id 不变，无 task.done/run.goal.completed。 |
| 上述轨迹在 G2 后 reopen，再发旧结果 | 拒绝依据来自持久化合同；不靠旧 runtime 内存缓存。 |
| G2 → dispatch A2 → old result(G1,A1) → current result(G2,A2) | A1 被真实 dispatch supersede；旧结果不 settle A2。新结果通过 admission，A2 succeeded，终结事件指向新结果；验证并非全部结果都拒绝。 |
| G1 → G2 → G3，混合历史结果/重开/重复拒绝 | 当前代保持 G3；每个旧结果不改变 current attempt、活动 dispatch 与业务完成事实；预期拒绝审计允许增加。 |
| current result 成功 → 下一次 dispatch | 若同 lane，series 才增长且 ordinal 归 1；generation 不因成功自动增长。 |

首条轨迹是关键：若每次 rollover 同时更换 dispatch，就只能证明已有 attempt 保护，无法证明合同换代 gate 接线。正向结果应携带新代实际 revision，并断言真实 `task.attempt.succeeded` 与 Store；只检查“没有 block”不足以证明它被处理。[admission 先后顺序](../../../../src/zf/runtime/orchestrator.py:2450)、[settlement](../../../../src/zf/runtime/task_attempt_runtime.py:365)

参考模型最小字段：`generation_epoch`、每代固定 `task_map_ref/generation/revisions`、活动 attempt 索引、每个 attempt 的 ordinal/series/status、fake seconds/expiry、预期 Task 状态/active_dispatch_id、获准 result ID 集合、预期 settlement 数。生产 Store 只作断言对象；不要调用生产 admission 判定来生成 oracle，也不要从实际 status 反推参考状态。[原模型结构](../../../../tests/conformance/test_zaofu_testkit_s11_attempt_model.py)

## 问题去重与误报边界

已查 [ZaoFu_issue.md](../04_audit_and_issue_ledger/ZaoFu_issue.md)：ZF-TEST-033 是预算耗尽后相同 dispatch 重放产生重复 deadletter；它与 generation rollover 不同，应继续保留原 strict xfail，不能借换代测试重复登记。现有记录未见同名“旧 task-map completion 结算当前 attempt”问题。

本轮仅有待复现候选，不能登记已证实 bug：`_cancel_superseded_tasks()` 取消旧 Task 时未显式终结 TaskAttempt；若取消前 A1 仍 sent，旧结果的 attempt 身份可能仍匹配。需在真实 superseding task-map ingestion、当前 Run admission、完整 lifecycle caller 下验证旧结果是否实际 settle/推进；静态缺少单个检查不足以下结论。[取消入口](../../../../src/zf/runtime/product_delivery.py:1001)、[TaskAttempt admission](../../../../src/zf/runtime/task_attempt_runtime.py:301)

另一个源码事实不是本轮确定缺陷：普通 capsule gate 不独立比较 payload.task_map_generation。若构造“旧 generation 但抄入当前 revisions”的混合身份，要先确定当前结果生产入口的权威合同；该输入不等同于真实旧结果。CallResult/Task Pipeline 存在另外的 currentness 校验，应单独追踪其接入链，不能由本小模型宣称覆盖。[call_result_authority.py](../../../../src/zf/runtime/call_result_authority.py)、[S3 selectors](../../../../tests/conformance/test_zaofu_testkit_s3_generation.py)

`task.completion.stale_rejected` 后续被 `_on_completion_stale_rejected()` 消费会合法申请同 lane rework；已有测试断言新 dispatch。因此旧结果拒绝后的“无推进”应限定于不采用旧成果、不结算当前 attempt、不进入成功后继，不能把所有将来的 rework dispatch 都断言为 bug。[rework handler](../../../../src/zf/runtime/orchestrator_reactor.py:2968)、[既有 rework 回归](../../../../tests/test_task_capsule_revision_gate.py:183)

完成声明应限定：新增 task-map/合同换代的顺序状态模型及真实消费者准入；不声称完整 fanout generation、Task Pipeline generation、真实 Provider、进程 crash、并发 race 或全流程成功。此报告只有源码证据与测试方案，无 pytest 通过结论。
