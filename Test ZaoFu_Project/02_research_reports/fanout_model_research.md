# TestKit §13：真实 fanout/fanin 生成模型入口研究

> 文档定位（2026-09-30）：历史调研，正文“当前/建议/尚未实现”仅适用于各段所记时点，不是最新执行队列。现行方案见 [ZaoFu_TestKit.md](../03_plans_and_steps/ZaoFu_TestKit.md)，执行仍按既有暂停/跳过边界；本次不重跑或改写历史结果。
>
> 当前对齐入口：`tests/conformance/test_zaofu_testkit_s11_fanout_model.py`。已有 fanout/fanin 生成式测试入口；仍保留有界动作空间和后端范围限制。

2026-09-14。范围：只读当前源码与测试，仅新增本研究文件；未执行测试、Provider、容器或远端操作。以下“现有测试”表示读到用例，不表示本轮复跑通过。优先实现 2–3 个 reader child 的成功、失败、重复、服务重开、取消和新 run；retry 与业务 rework 独立扩展。要求来源：[TestKit §13](../03_plans_and_steps/ZaoFu_TestKit.md)。

## 1. 最小真实入口

- 配置复用 [test_reader_fanout_runtime.py](../../../../tests/test_reader_fanout_runtime.py) 的 `_config(mode="wait_for_all", synth=True)`：两名 mock reader、一名可选 synth；或 [test_fanout_hardening.py](../../../../tests/test_fanout_hardening.py) 的 `_synth_budget_config`。第三个 child 只需增加 role 与 stage.roles；不启用 task-map、writer worktree 或 plan-synth profile。
- mock 复用上述文件的 `_RecordingTransport`：记录 `send_task`，`is_alive=True`、空 `capture_log/poll_events`。状态目录使用 [scenarios.py](../../../../tests/testkit/scenarios.py) 的 `isolated_project(tmp_path).state_dir`，并设置 `config.project.state_dir`；`Orchestrator(..., project_root=tmp_path)` 显式传隔离根。Task/Session 初始化分别使用 TaskStore/SessionStore，不照抄旧 `_state` 的 `.zf` 与直接写 kanban。
- `EventWriter.append(candidate.ready)` 后 `orch.run_once(events=[event])` 是真实发起路径；trigger ID 每代前 12 字符也不同，避免轻 fixture 无 identity_scope 时的 ID 截断碰撞。`FanoutContext.create` 的 ID 规则见 [fanout.py](../../../../src/zf/runtime/fanout.py) 55–88 行。
- 真实调用链：`run_once → _react_to_events → _apply_housekeeping → _maybe_start_reader_fanout/_maybe_update_reader_fanout`；见 [orchestrator.py](../../../../src/zf/runtime/orchestrator.py) 864、2422、3498 行与 [orchestrator_fanout.py](../../../../src/zf/runtime/orchestrator_fanout.py) 2595、4668 行。
- kernel 自动写 `fanout.requested/started/child.dispatched`。`EventWriter` 的 fanout hook 驱动 `FanoutManifestProjector`；测试不能自行注入这些 kernel 结果来证明真实 dispatch。[writer.py](../../../../src/zf/core/events/writer.py) 263–280 行。
- 完成输入采用 `workflow.child.completed/failed`，payload 带实际派发得到的 `fanout_id/child_id/run_id`、workflow_run_id、status；actor 是实际 role。从派发结果读取身份只用于调用真实系统，参考模型的预期 child 数和终态来自生成动作。
- `run_once(events=[])` 执行周期恢复；`run_once()` 还按持久 offset 消费账本。首版 reopen 用新 EventLog、新 Orchestrator 指向同一 state_dir、同一 recording sink，再空 tick/按 offset 消费；明确只是服务对象重开，并非进程崩溃或业务任务重开。[orchestrator.py](../../../../src/zf/runtime/orchestrator.py) 864–937 行。

现有可读基线：[test_zaofu_testkit_s3_fanout.py](../../../../tests/conformance/test_zaofu_testkit_s3_fanout.py) 已有“逆序完成、重复回调、等待全体再派发 synth”及“一失败后 synth reject，aggregate/review.rejected 各一次”。它没有生成轨迹、重开、取消或新 run。

## 2. run-cancel 最小受控动作

取消前必须有可识别 run。复用 [test_run_admission.py](../../../../tests/test_run_admission.py) 的 `_invoke_event/_invoke`（152 行）与 `_control`（193 行）：TaskStore 中建立 root Task，带 `active_dispatch_id` 和 `contract.evidence_contract.workflow_run_id`，再真实处理 `workflow.invoke.requested`。其核心 payload 为 `request_id/run_id/workflow_run_id/task_id/pattern_id/dispatch_id/requested_by/reason/source/source_refs`。该文件提供可执行 fixture，不能只发 candidate.ready 后假定 run-control 一定查得到 run。

```python
requested = orch.event_writer.append(ZfEvent(
    type="control.action.requested", actor="operator",
    correlation_id=run_id, payload={"action": "run-cancel", "run_id": run_id},
))
result = ControlledActionService(
    orch.state_dir, orch.event_writer, config=orch.config,
    project_root=orch.project_root, actor="operator", source="test", surface="web",
).execute(
    action="run-cancel", requested_action="run-cancel",
    payload={"run_id": run_id, "reason": "model cancellation"}, requested=requested,
)
assert result["ok"]
cancel_event = next(e for e in orch.event_log.read_all() if e.id == result["event_id"])
orch.run_once(events=[cancel_event])
```

`run-cancel` 可对 requested/queued/running run 取消；重复调用应返回 `idempotent_replay=True` 且 `run.cancelled` 一次。取消后的 run 不能 `run-resume`；新一代使用新 run_id、新 trigger，并通过 admission。[control_actions_run.py](../../../../src/zf/runtime/control_actions_run.py) 31–166 行、[test_run_admission.py](../../../../tests/test_run_admission.py) 600–677 行。

`run.cancelled` 的 housekeeping（[orchestrator.py](../../../../src/zf/runtime/orchestrator.py) 3059 行）及首次 idle sweep 调用 `reconcile_cancelled_run_resources`，通过 EventWriter 产生 `fanout.cancelled`，关闭关联 operation/task、释放 child worker。idle sweep 带 `_run_cancel_resources_reconciled` 缓存，因此 service 取消后应明确递送 cancel event，不能只依赖已运行过的实例再次空 tick。[run_cancel_reconciliation.py](../../../../src/zf/runtime/run_cancel_reconciliation.py) 29–230 行。

同 stage/target_ref/task/pdd/feature 的后发 fanout 才 supersede 旧实例；logical_key 本身不含 workflow_run_id。跨新 run 使用不同 root Task 时不要预期 `superseded_by_latest_fanout`，应检查旧 run 的 terminal fence 与新 run 的独立进展。[fanout_identity.py](../../../../src/zf/runtime/fanout_identity.py) 150–185、233–283 行。

## 3. 独立参考 oracle

模型仅保存 `generation、active_run、cancelled、expected_children、child_outcomes、synth_expected、synth_accepted、aggregate_outcome、processed_action_keys`，初始 child 数由策略选定，最多两代、每代 2–3 个 child、每轨迹 15–25 步。固定样本预算与 Hypothesis deadline；每步检查，而非只看最终状态。[TestKit §13.2–13.3](../03_plans_and_steps/ZaoFu_TestKit.md)。

| 模型动作 | 独立预期与真实观测 |
|---|---|
| start | configured child 集合恰好派发一次；manifest planned children 与 sink role 调用对应 |
| child success/failure | 当前 child 从 pending 进入唯一 terminal；重复同结果与同回调均无新增 terminal/synth |
| all children terminal | `wait_for_all` 才允许一次 synth dispatch；尚无 aggregate completion |
| synth approve/reject | 仅派发后接受一次；aggregate completed/failed 与 publish 成对，拒绝后的失败可由 synth 决策决定 |
| cancel | run/fanout terminal 为 cancelled；取消后旧 child/synth 无新成功聚合、副作用或新派发 |
| reopen | 语义投影、已终态 child、dispatch/synth/aggregate 计数不倒退、不重复 |
| new run | 新 generation 的派发与完成不被旧 run 的迟到结果、重复取消或重开放大/阻断 |

无 synth 的聚合规则是全 terminal 后“任一 failed 则 failed”；`any_failed_fail` 可首失败即终止。带 synth 的路径仍要求全 terminal，最终 recommendation 决定结果；不能将“一个 child failed”硬编码为 synth 必须 reject。[orchestrator_fanout.py](../../../../src/zf/runtime/orchestrator_fanout.py) 5875–5921、7182–7225 行。

oracle 禁止调用生产 `_evaluate_reader_fanout`、identity projector 或 manifest reducer 来算 expected。读取 manifest、事件、transport 是三类实际结果；比较模型与这些结果，再把 `FanoutManifestProjector.rebuild` 冷重放作为额外一致性检查。[fanout.py](../../../../src/zf/runtime/fanout.py) 341–713 行。

现成 [fanout_replay.py](../../../../src/zf/runtime/fanout_replay.py) 的 `record_fanout_fixture/replay_fanout_fixture` 两边使用同一 reducer，仅证明重放一致性，不能充当独立 oracle；且按 fanout_id 过滤会遗漏只带 run_id 的取消/admission 上下文。失败保存完整账本、操作序列、seed/trace、模型状态、manifest、sink 调用，不仅保存它的 fixture。

## 4. 必须固定的轨迹

1. `start → B success → duplicate B → reopen → A success → synth approve → duplicate synth → reopen`；断言 barrier、synth/aggregate/publish 各一次。
2. `start(3 children) → C failed → A success → reopen → B success → synth reject`；每个前缀均不提前聚合，失败 publish 一次。
3. `start → one child success → cancel → duplicate cancel → reopen → old pending-child success → old synth approve`；无取消后成功聚合，资源保持终态。
4. `start R1 → cancel R1 → admit/start R2 → late R1 result → duplicate old cancel → complete R2`；旧代不推进新代，新代正常结束。
5. `start → synth approve before all children terminal`；负向协议探针，应拒绝提前完成。见下面未复现候选 A。
6. 每条固定轨迹对应生成规则都保留合法前置条件；额外放一个故意错误 expected/canary，确认 oracle 会捕获违反 barrier 或重复 dispatch，而非始终自洽。

## 5. retry、业务 rework 和 Task Pipeline generation 的独立边界

- child retry 真实路径是 `run_once([]) → _check_fanout_timeouts → _retry_fanout_child`：未终态 child 超时、`dispatch_count <= max_retries` 才允许重试；fanout_id/child_id 不变、run_id 变并记录 retry_of_run_id，最多 `1 + max_retries` 次。[orchestrator_fanout.py](../../../../src/zf/runtime/orchestrator_fanout.py) 6402、6515–6638、6861 行。
- 现有 [test_fanout_hardening.py](../../../../tests/test_fanout_hardening.py) 971 行用 timeout=1/max_retries=1、假 `_now`，但直接 `_set_worker_state(..., idle, force=True)`；该动作只能标为 fixture 控制，不当作业务重开。首版无需为了覆盖它扩大成 Provider 生命周期模型。
- 真实 task rework 基线是 [test_zaofu_testkit_s3.py](../../../../tests/conformance/test_zaofu_testkit_s3.py) 的 `test_review_failure_routes_bounded_rework_and_preserves_reason` 和 retry-cap 用例：合法 review task + review.rejected → task.rework.requested/task.dispatched。reader fanout `max_retries` 是超时重试预算；不能声称它覆盖语义 rework cap。
- Task Pipeline 的 generation admission/候选冻结属于另一协议；[test_zaofu_testkit_s3_generation.py](../../../../tests/conformance/test_zaofu_testkit_s3_generation.py) 已测 run/current generation 与 operation identity，reader caller 的旧代抑制在 [fanout_retrigger_guard.py](../../../../src/zf/runtime/fanout_retrigger_guard.py) 16–96 行。首版“新 run”不能等同于此协议已覆盖。
- [test_reader_fanout_runtime.py](../../../../tests/test_reader_fanout_runtime.py) 4159 行有 stale generation 取消测试，但直接改 manifest 绑定 operation；可借 payload/入口，不能照搬 mutation 作为新模型动作。writer handoff 的失败→完成恢复另见 [test_fanout_recovery_runtime.py](../../../../tests/test_fanout_recovery_runtime.py) 105 行，依赖提交/workdir，不纳入轻 reader 模型。

## 6. 已知问题去重与未复现候选

[ZaoFu_issue.md](../04_audit_and_issue_ledger/ZaoFu_issue.md) 的 ZF-TEST-031 是独立 dependency-barrier 后下游 synth 未派发；不是本模型 fanout aggregate 的 synth。ZF-TEST-002/003/007 是 dispatch 崩溃/并发窗口；ZF-TEST-015 是 fanout artifact TOCTOU；ZF-TEST-028/029 是 wake/housekeeping；ZF-TEST-033 是 TaskAttempt deadletter 重复；ZF-TEST-034/035 是 EventLog 多实例索引。发生相同真实入口与触发时序时沿用 Issue，不把本轮服务重开一概归为新 fanout bug。

- 候选 A（未复现）：普通非 plan-synth 的 `fanout.synth.completed` 经过 `_handle_fanout_synth_completed` 到 `_finalize_fanout_synth`，后者检查 currentness/aggregate terminal，但该段未检查 synth 已派发或所有 child terminal。负向最小轨迹是发起后立即 synth approve。[plan_synth_runtime.py](../../../../src/zf/runtime/plan_synth_runtime.py) 706–776 行、[orchestrator_fanout.py](../../../../src/zf/runtime/orchestrator_fanout.py) 7182–7225 行。上游 lifecycle/owner checkpoint 是否拦截，须真实 run_once 复现。
- 候选 B（未复现）：普通 reader `_maybe_update_reader_fanout` 采用 payload.run_id，未在早段直接比对当前 child.run_id；retry 后旧 run_id 结果可能错误推进。后续 call-result admission/lifecycle 可能拦截，因此只能用实际旧 run_id 固定轨迹确定，不能从省略一个检查直接宣告缺陷。[orchestrator_fanout.py](../../../../src/zf/runtime/orchestrator_fanout.py) 4730–4774、4888–4940 行。
- projector 不提供上述拒绝：`fanout.child.completed` 直接覆盖 child status/run_id，`fanout.synth.completed` 写 synth，`fanout.aggregate.completed` 写 aggregate/status；无 all-children barrier 或当前 run_id 的校验。它是事实投影，真实 admission 才是应验证的边界。[fanout.py](../../../../src/zf/runtime/fanout.py) 548–681 行。

后续任何真实失败先保存固定反例、完整证据并去重登记；本轮仅加测试/支撑，不修改生产实现，也不以 xfail 计为产品通过。
