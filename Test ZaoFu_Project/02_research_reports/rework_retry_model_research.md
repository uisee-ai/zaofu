# 业务 Rework 与 Retry 耗尽模型：源码调研

> 文档定位（2026-09-30）：历史调研，正文“当前/建议/尚未实现”仅适用于各段所记时点，不是最新执行队列。现行方案见 [ZaoFu_TestKit.md](../03_plans_and_steps/ZaoFu_TestKit.md)，执行仍按既有暂停/跳过边界；本次不重跑或改写历史结果。
>
> 当前对齐入口：`tests/conformance/test_zaofu_testkit_s11_rework_model.py`。已有 rework 生成模型；生产问题修复状态仍以台账为准，未复现组合不新增结论。

日期：2026-09-14。范围仅为当前工作树 ZaoFu 自身；第一方源码和现有测试，不调用 Provider、容器或远端，不修改生产实现。此文是 §13 生成式模型的实施依据，不是全量验证报告。

## 1. 先分开四条真实路径

| 路径 | 预算/身份 | 真实入口与边界 |
| --- | --- | --- |
| Task-local 产品失败 rework | `Task.retry_count`、role/backedge `max_rework_attempts`、failure fingerprint | `run_once` → housekeeping → reactor → `_route_rework_trigger` → `_dispatch_rework` |
| Scheduler TaskAttempt | canonical TaskAttempt identity 五元组、`WorkflowTaskAttemptConfig.max_attempts` | `_send_transport_task` → prepare/send/settle；ZF-TEST-033 属于此预算，不等同产品失败次数 |
| Reader timeout retry | fanout child 的 `retry_count/max_retries`、child run_id | fanout periodic sweep → `_retry_fanout_child`；已有上一阶段固定正负例和 ZF-TEST-037 |
| Task Pipeline / candidate rework | operation_generation / task_map_generation / plan package / fingerprint | pipeline reconciliation 或 candidate sweep；不是 legacy task rework 的别名 |

源码没有 `orchestrator_rework.py`。实际所有者见 [orchestrator_dispatch.py](../../../../src/zf/runtime/orchestrator_dispatch.py)、[housekeeping.py](../../../../src/zf/runtime/housekeeping.py)、[task_pipeline_rework.py](../../../../src/zf/runtime/task_pipeline_rework.py)、[candidate_rework.py](../../../../src/zf/runtime/candidate_rework.py)。下文以仓库根路径及函数名定位，避免把旧设计名称当现实现。

## 2. 最小 Fixture 与真实调用

- 首选 `tests/conformance/test_zaofu_testkit_s3.py::orchestration_context`：自定义 `state/`、Session active、dev/review mock roles、dry-run TmuxTransport，无真实进程。该文件已有单次 rework、同对象重复 wake 和预置 count 的 cap 测试，但未生成完整的多轮成功/失败轨迹。
- 若要联合验证真正的 TaskAttempt Store 和 transport 次数，可复用 `tests/test_scheduler_task_attempt.py::_RecordingTransport(state)`；它记录 `contexts`，读取真实 TaskAttemptStore，不启动 Provider。
- 初始 Task 可合法用 `TaskStore.add(Task(...status='review', assigned_to='dev'))` 建立 Given；后续状态不得再通过 Store 预置来冒充生成式恢复。
- 后续事件必须先经 `runtime.event_writer.append`，再 `runtime.run_once(events=[event])`；不要只调用 `_dispatch_rework`，否则漏掉 retry 计数、triage、生命周期准入。
- 对象重开：同 root/state/config/transport 重新 `Orchestrator(...)`；只证明持久状态重读，不是操作系统崩溃测试。

已实际运行的最小轨迹核心（使用上述 fixture，临时目录自动清理）：

```python
project, store, log, runtime = orchestration_context.__wrapped__(tmp_path)
runtime.config.roles[0].max_rework_attempts = 1
store.add(Task(id=project.task_id, title="bounded", status="review", assigned_to="dev"))
failure = runtime.event_writer.append(ZfEvent(
    type="review.rejected", actor="review", task_id=project.task_id,
    correlation_id=project.run_id, payload={"reason": "functional mismatch"},
))
runtime.run_once(events=[failure])
runtime.run_once(events=[failure])
runtime = Orchestrator(project.state_dir, runtime.config,
                       runtime.transport, project_root=tmp_path)
runtime.run_once(events=[failure])
```

此片段是 legacy/shadow fixture。若切到 enforce，worker 输入须从实际 dispatch context 得到 `dispatch_attempt_payload(context)`，并附当前 source/contract/capsule revisions；不能把 actor 改成 zf-cli 绕过准入来声称覆盖真实 worker。

## 3. 确切预算和终态 Oracle

- `src/zf/runtime/housekeeping.py::apply_rework_failure_event` 仅 `_REWORK_FAILURE_TYPES` 且有 task_id 时加一；前置 `_ensure_rework_triage` 的 `should_increment_retry` 和合法 task 状态决定是否调用。evidence/harness/environment 不能一概算产品失败。
- 同 fanout_id 的重复 failure 会在 housekeeping 去重；无 fanout_id 时该函数本身没有持久 event-id 去重。
- `orchestrator_dispatch.py::_dispatch_rework` 使用 `max(Task.retry_count, counted_rework_rounds(events, task.id)) > max_attempts` 拒绝。因此 cap=1 允许第一轮 rework，第二次有效产品失败才 cap；不是 `>=`。
- backedge 非零 `max_attempts` 优先于 role；`task.attempt.retry_scheduled` 写入 ordinal=`cap_count+1`、cap、cap_source、lease_token=新 dispatch_id，不能误断言 ordinal==retry_count。
- `dispatch_recovery_policy.py::_semantic_rework_triage_required` 是独立限制：同 fingerprint 的有效 failures 达到 `workflow.strict_triggers.rework_attempts_gte` 时停止盲派；默认0关闭。已存在同 fingerprint 的 semantic cap 后不会再写同类 cap。
- `dispatch_routing_queries.py::_circuit_for` 是额外熔断器：`max_failures=role.max_rework_attempts+2`，窗口1800秒，持久文件 `circuits.json`。生成式测试应固定时钟；不得关闭此真实门禁掩盖不变量失效。
- cap 的正确事实是无新 transport/dispatch/rework、副作用受限、`task.rework.capped` 绑定 source failure/causation、`recovery_owner=run_manager`；当前实现可以保留 Task `in_progress`。cap 是停止自动重做/移交恢复，不等于 Task failed 或 Run terminal。
- 证据：`tests/test_rework_cap.py::TestCapBlocksRework`、`TestAttemptRetryScheduledEvent`；`tests/test_run_manager_rework_triage.py` 的 semantic threshold/cap 用例。

建议参考模型独立持有：已接受失败 event IDs、每轮 failure fingerprint、产品 retry_count、派发 contexts/新旧 dispatch IDs、Task 当前状态、Attempt 各身份/状态、已settle source IDs、cap事件来源、worker占用/释放、禁止终态及 transport 次数。不要调用生产 `counted_rework_rounds` 来计算期望；它是被测对象。

## 4. 成功结果的 Delta 与旧身份准入

- `orchestrator_reactor.py::_rework_delta_missing`：仅开启 `verification.contract.rework_delta_required`、Task.retry_count>0、存在 task.rework.requested 时检查。
- required_actions 必须先全部有覆盖；之后 non-empty files/commits/artifact_refs/evidence_refs/changed_files/patches，Task evidence，或基于 rework 起点的新 Git diff 可作为当前机械 gate 的 delta。
- 没有 required_actions 时，非空 no_code_rationale/no_code_change_reason 也是明确的允许分支。不能用“所有成功必须改代码”作 oracle。
- 必须测试 dirty baseline 不变失败、baseline 后新增变化通过；不能把 dispatch 前已存在 dirty files 当作这轮修复。
- 无 delta 或 required_actions 未覆盖：保持 in_progress，`discriminator.failed` 的 failed_d 包含 ReworkDeltaD；正向例可进入 review，但不得擅自完成 Task/Run。
- 机械 gate 接受 payload 声明不代表验真文件内容/运行测试；应与独立 evidence/ref/digest 测试并列，不夸大此处证明能力。
- 现有可复用轨迹：`tests/runtime/test_rework_routing.py::test_rework_no_delta_blocks_success_event`、`test_rework_delta_payload_allows_success_event`、两个 dirty baseline/delta 测试、`test_rework_required_actions_must_be_covered`。
- `orchestrator_reactor.py::_reject_invalid_lifecycle_event` 在 housekeeping 之前执行；`runtime_authority_bridge.py` → `task_attempt_runtime.validate_task_attempt_result` 在 enforce 检查 run/operation/attempt/lease/dispatch 身份、active状态和lease有效期。
- 旧 generation 不应只改一个字符串：参照 `tests/conformance/test_zaofu_testkit_s11_generation_model.py`，用真实 `ingest_task_map_to_kanban` 更新 task-map/contract，并保存前代 dispatch context 和 revisions。该已有模型明确不是 Task Pipeline 模型。

## 5. 建议实现清单（由主代理实施）

1. [ ] 固定轨迹：review失败 → 第1次真实rework派发 → 当前结果带合法delta进入review → 新failure → cap，无新增派发；每步联合 Task/Attempt/events/transport。
2. [ ] 生成动作：有效failure、当前成功、无delta成功、duplicate原event、同身份新event、同目录reopen；限制2–4轮且固定clock，失败保存动作与原始Store/EventLog。
3. [ ] 同对象与重开后的重复failure均不得耗费额外产品预算；重复结果不得重复settle、追加有效dispatch或触发假cap。
4. [ ] 在相同/不同 fingerprint、正常产品失败/evidence gap/环境问题之间生成输入；分别检查预算和路由，不把环境错误当产品失败。
5. [ ] cap 后有限重复wake及重开仍保持停止自动重做；如要求真正Run终态，必须另接 Run Manager 的批准恢复/终止路径，不能仅依据capped事件宣称已完成。
6. [ ] stale result 分固定旧attempt、旧contract、混合身份；保持当前Task/Attempt/预算、验证拒绝causation。先复用已完成generation模型，再补真正pipeline换代。
7. [ ] oracle canary 用测试侧扰动证明断言能发现多一次派发/预算漂移，不能修改生产实现；已知bug仅特定断言严格xfail，外层结果仍fail。
8. [ ] 与 S0/S1/S3/S10、rework routing/cap、attempt lifecycle/generation、必要 mock E2E 联调，保留收集/退出/清理证据。

## 6. 本次发现与未证实候选

已实测异常（尚未正式登记 Issue，交由主代理复现）：上述 cap=1 fixture 初次 failure 后 retry_count=1、dispatch=1、rework=1；同对象再次送同一个已落盘 event 保持不变；同目录新 Orchestrator 再送相同 event 后 retry_count=2、capped=1，而 dispatch仍1。源码 `_processed_event_ids` 为实例内集合（`orchestrator.py:421,2448`），housekeeping无非fanout持久去重，与观察一致。

边界说明：此复现是显式重复已落盘 event，尚未证明正常 EventWatcher consumed_offset 路径会自然重送。正式 detector 应分别证明显式at-least-once批次与正常offset恢复；不得宣称所有正常重启必然重复。没有修改 src、测试或 Issue 文档。

与已登记问题去重：ZF-TEST-033 是 TaskAttempt prepare budget 耗尽后重复deadletter；ZF-TEST-037 是 reader旧run结果污染当前manifest；本异常是业务 retry_count/cap，非同一状态所有者。ZF-TEST-007 是dispatch前崩溃导致Task状态提前变更；本次没有注入提交边界崩溃。

尚未复现候选：无fanout重复账本行可能被 `attempt_ledger.counted_rework_rounds` 多计；缺delta结果是否提前settle attempt；新generation后旧failure是否进入housekeeping。只能作为待测项，不能据静态阅读登记已存在bug。

## 7. 明确未覆盖的 Task Pipeline 换代

`task_pipeline_rework.py::derive_impl_rework_requests` 从真实已settled的impl operation、result、task.ref.rejected 导出rework；必须匹配workflow_run_id/task_map_generation/operation_generation。stale_contract_result 不应重开 impl；task.ref.updated 可解除旧拒绝，见 `tests/test_task_pipeline_rework.py`。

这些是下阶段真实pipeline caller模型的材料，不能把 reducer 单测、预置operation dict、legacy dev重派或reader timeout当成pipeline端到端换代证明。Candidate 指纹模式还受 `goal.rework_fingerprint` 开关控制，见 `tests/test_rework_fingerprint_counting.py`；它同样需要独立candidate sweep接线后才能纳入完整证明。
