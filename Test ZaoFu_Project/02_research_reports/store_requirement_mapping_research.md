# K2 / STO：Canonical Store 要求与已注册测试映射

> 文档定位（2026-09-30）：历史调研，正文“当前/建议/尚未实现”仅适用于各段所记时点，不是最新执行队列。现行方案见 [ZaoFu_TestKit.md](../03_plans_and_steps/ZaoFu_TestKit.md)，执行仍按既有暂停/跳过边界；本次不重跑或改写历史结果。
>
> 当前对齐入口：`tests/conformance/test_zaofu_testkit_s2_projection_rebuild.py`。重建只覆盖明确 projection 合同，不能由 EventLog 推导任意 canonical Store/必需 sidecar 可丢弃。

2026-09-14；基于 HEAD `142a62a` 的当前工作树（含主线程 K1 注册增量）。按 research 技能只读核查 §7、S2/S3/S4 清单和相关 Store 源码/测试；本轮仅新增本文件，未执行测试、网络、Provider 或子代理。下文“已注册”仅表示 registry 明确包含模块/节点，不代表本轮测试通过。

## 1. 逐条映射

原始要求见 [TestKit §7](</home/uisee/zaofu_v7/awesome-zaofu/New_function/Test ZaoFu_Project/03_plans_and_steps/ZaoFu_TestKit.md:298>)；注册依据为 [S2](/home/uisee/zaofu_v7/tests/testkit/suites.py:25)、[S3](/home/uisee/zaofu_v7/tests/testkit/suites.py:62)、[S4](/home/uisee/zaofu_v7/tests/testkit/suites.py:83)。以下缺口均限定于本次 Store 清单，不声称全仓库不存在其他测试。

| 要求 | 已存在的实际行为节点 | 注册情况 | 真实缺口/边界 |
|---|---|---|---|
| STO-01 创建/查询/列表，字段/ID/scope/状态 | `test_task_store.py::test_create_and_list`、`test_get_by_id`、`test_filter_by_status`、`test_persistence_across_instances`；S2 `test_task_store_state_and_atomic_file_are_readable` 实际 add/get/update | S2 最小读写已注册；完整 TaskStore 文件未注册 | 最小节点只验证 status 与换行；完整文件也没有带 scope/完整 contract 的独立字段往返 oracle。需补登记并补完整 Task 字段快照 |
| STO-02 合法迁移 | `test_task_statemachine.py::test_valid_forward_transitions` / `test_transition_returns_new_state` / `test_blocked_transitions`；S3 `test_ready_task_dispatch_updates_ledger_and_store`、`test_provider_completion_moves_task_to_review_without_terminal_completion` | 纯状态机未注册；两个实际 run_once 节点已注册 S3 | S3 证明 backlog→in_progress、in_progress→review；不覆盖整个迁移表。blocked→backlog 是定义的合法解阻，不能把“forward”误读为线性序号单增 |
| STO-03 非法回退/普通 worker 重开终态被拒，Store 不变 | 纯状态机 `test_backward_transition_rejected` / `test_backward_from_testing_rejected` / `test_cancel_from_done_rejected`；TaskAttempt `test_attempt_terminal_is_monotonic_in_shadow_mode` 实际结算相反结果后检查 Store 和终态事件 | 上述节点均未显式注册；S3 provider completion 只证明不能自报 run completion | 纯函数没有 Store I/O，不能证明 canonical 字节不变；需真实状态消费入口的终态旧事件负例。显式授权 reopen 是合法例外，见下文 |
| STO-04 Feature/Session/Role 所有权 | `test_feature_store.py` CRUD/状态枚举/原子写；`test_session_store.py` Session/worker 字段持久化；`test_role_sessions.py` 按 project/role 隔离、持久化 | 三模块已注册 S2 | 使用所属 Store 的正向测试不证明所有外部组件都被禁止绕过。Role 按 instance/backend 的进一步合同未注册；STO-10 审计只覆盖部分语法/目录 |
| STO-05 原子写，失败保留完整旧文件 | Task `test_save_is_atomic_no_partial_writes`；S2 multiprocess 的 `test_task_store_subprocess_crash_before_replace_preserves_snapshot`；S2 store_crash 的 feature/session/role 三参数；S4 `test_store_replace_failure_preserves_last_complete_snapshot` | Task 原文件未注册，但同域 crash 节点已注册 S2/S4；Feature/Session 原子节点已注册 | 对四类基础 Store 已有真实 replace seam 与重新读取证据，不应重复写等价测试；TaskAttempt 独立原子故障矩阵在本清单未见。通用 snapshot helper 的测试不能自动计为每种 Store 的 caller 覆盖 |
| STO-06 并发更新不丢失 | `test_state_locks.py::test_task_store_concurrent_adds_do_not_lose_updates`、`test_role_session_registry_concurrent_writes_preserve_all_entries`；S2 multiprocess `test_task_store_concurrent_update_survives_one_crashing_writer` | 前两节点未注册；Task 同时崩溃/健康 writer 已注册 S2 | Task 基础并发与 Role 并发可直接复用；本清单没有 Feature、Session 独立跨进程非冲突字段/记录更新 oracle。并发 add 不等同于同一行不同字段 update |
| STO-07 archive/terminal 查询一致、最新 attempt 明确 | Task/Feature terminal archive 两模块实际比较 active/archive/get；TaskStore `test_get_terminal_task_returns_latest_archived_attempt`；TaskAttempt `test_current_for_task_prefers_the_only_active_run_attempt` / `test_retry_order_survives_sorted_json_reload` | 两 archive 模块、TaskStore 节点及两 scheduler 节点未注册；S2 仅 terminal-index 中断后不丢 Task 已注册 | 补登记能覆盖大部分缺口；Task 历史快照的“attempt”与 scheduler TaskAttempt 不同，必须分别标注。active list 排除 terminal 是正确合同，不要求与含 archive 列表完全相同 |
| STO-08 损坏 fail-closed，不从低权威 projection 猜状态 | `test_task_attempt_recovery.py::test_corrupt_canonical_store_fails_closed_instead_of_projection_fallback`、`test_canonical_store_wins_over_conflicting_projection`；scheduler 的 malformed JSON/空 row/scope mismatch 节点 | 前两已注册 S4；scheduler 三节点未注册 | TaskAttempt 有明确合同；Task/Feature/Session/Role 基础模块没有损坏输入矩阵。不能把文件缺失、replace 失败或损坏 projection 的自动重建当成 canonical corruption 测试 |
| STO-09 replay 等价 | S3 spine `test_corrupt_projection_rebuilds_from_zero`；pipeline `test_projection_replay_is_stable_and_worker_comes_from_operation`；S4 workflow resume projection 节点；`test_i1_rebuild.py` cost/terminal-index 重建 | 前三已注册 S3/S4；i1 未注册；S2 snapshot helper 已注册但非重建 | 已有真正 projection 重建与纯函数稳定性；spine 仅断言一条计数，pipeline 从相同完整输入重算，S2 人工重复 state。需实际投影删除/重建后完整语义快照比较，禁止删除 canonical Store |
| STO-10 非所属组件直接文件写入审计 | S2 surface `test_integrations_and_web_do_not_write_canonical_files_directly` 对 integrations/web AST 扫描固定文件名与 write_text/write_bytes/unlink/rename/replace | 已注册 S2 | 没覆盖 runtime/providers、`open(..., 'w')`、写函数参数变量/别名、atomic helper 间接路径，canonical 名单也无 task_attempts.json。这是有界静态守卫，不能声称完整所有权证明 |

## 2. 源码所有权与合法例外

`TaskStateMachine` 是纯迁移函数，表内包括 blocked→backlog/in_progress，done/cancelled 无普通出口。[迁移表](/home/uisee/zaofu_v7/src/zf/core/statemachine/task.py:10)。`TaskStore.update` 是受锁的持久化原语，没有调用该状态机，且会对 terminal 写 archive/index；所以不能把对 Store 的任意 update 等同于 worker 的合法入口。[update](/home/uisee/zaofu_v7/src/zf/core/task/store.py:292)。

`TaskStore.reopen` 明确把 terminal Task 重新放回 active 并保留历史；`compare_and_update_contract(..., reopen_terminal=True)` 又提供带 authority revision 的受控合同路径。[reopen](/home/uisee/zaofu_v7/src/zf/core/task/store.py:268)、[CAS 重开](/home/uisee/zaofu_v7/src/zf/core/task/store.py:338)。STO-03 应验证“普通旧事件/worker 不得重开”，而非禁止一切显式 reopen。现有最新 archived attempt 测试正依赖合法 reopen。[测试](/home/uisee/zaofu_v7/tests/test_task_store.py:92)。

四类 Store 都通过原子 writer 保存，Task/Feature/Session/Role 更新路径都带各自文件锁；这说明 intended owner seam，不证明每个外部调用者都遵守它。[Task 保存](/home/uisee/zaofu_v7/src/zf/core/task/store.py:71)、[Feature 保存](/home/uisee/zaofu_v7/src/zf/core/feature/store.py:60)、[Session update/save](/home/uisee/zaofu_v7/src/zf/core/state/session.py:67)、[Role lock/reload](/home/uisee/zaofu_v7/src/zf/core/state/role_sessions.py:137)、[atomic_write_text](/home/uisee/zaofu_v7/src/zf/core/state/atomic_io.py:15)。

损坏形态必须分开：Task/Feature 的非法 JSON 会在 json.loads 抛错，但合法 JSON 非 list 根会静默变空列表；Role 非 dict YAML 根会静默返回空注册表；Session 空文件拒绝，其他 malformed YAML/结构错误由 parser/dataclass 暴露。[Task load](/home/uisee/zaofu_v7/src/zf/core/task/store.py:64)、[Feature load](/home/uisee/zaofu_v7/src/zf/core/feature/store.py:51)、[Role load](/home/uisee/zaofu_v7/src/zf/core/state/role_sessions.py:78)、[Session load](/home/uisee/zaofu_v7/src/zf/core/state/session.py:59)。这些是待正式测试的源码风险，不是本轮已复现 Issue；建议同时测试损坏语法、错误根形状和失败后原始字节不被覆盖。

TaskAttempt 的 parser 对非法 JSON、根类型、schema、row/current 映射有显式 `TaskAttemptStoreError`；恢复入口在 canonical 不可读时输出 human_escalate，而非读取旧 projection 猜状态。[parser](/home/uisee/zaofu_v7/src/zf/core/state/task_attempts.py:403)、[恢复实测节点](/home/uisee/zaofu_v7/tests/test_task_attempt_recovery.py:306)。

## 3. STO-09 的 replay 应解释到哪里

§7.2 原文操作是“删除可重建 projection”，不是删除 canonical Store。`tests/test_i1_rebuild.py` 的模块说明明确只证明 cost 和 terminal-task index 从各自声明输入重建，不证明 EventLog 可恢复所有 canonical runtime Store。[原要求](</home/uisee/zaofu_v7/awesome-zaofu/New_function/Test ZaoFu_Project/03_plans_and_steps/ZaoFu_TestKit.md:315>)、[既有边界说明](/home/uisee/zaofu_v7/tests/test_i1_rebuild.py:1)。

- `kanban-terminal-index.json` 从 canonical Task archive 重建，输入不止 EventLog；保留 archive 与完整 Task。最小加强：实际 TaskStore add→terminal，保存 get/ready/list_with_archive 结果，删除 index，调用真实 `rebuild_terminal_index_from_archive`，再次比较结果。现有 i1 仅构造旧式 archive 记录并检查私有 index loader。[实现](/home/uisee/zaofu_v7/src/zf/core/task/store.py:114)、[现有节点](/home/uisee/zaofu_v7/tests/test_i1_rebuild.py:54)。
- `state/projections/task_attempts.json` 是 spine 投影，`state/task_attempts.json` 是 scheduler canonical Store，同名不等于同一权限。[spine 路径](/home/uisee/zaofu_v7/tests/test_workflow_spine_projection.py:19)、[canonical 胜出](/home/uisee/zaofu_v7/tests/test_task_attempt_recovery.py:216)。
- S3 spine 的 corrupt→refresh 是真实生产重建，但当前仅断言 events_folded=1、attempt_count=1；可比较重建前后完整稳定 projection，并单独处理游标元数据。[节点](/home/uisee/zaofu_v7/tests/test_workflow_spine_projection.py:260)。
- S3 pipeline replay 的输入明确含 policy、Task、events、Attempt、session bindings；重复 builder 得到相同值是确定性投影合同，不是从 ledger 恢复被删 Store。[节点](/home/uisee/zaofu_v7/tests/test_task_pipeline_projection.py:166)。
- S2 `test_replay_snapshot_is_semantically_equal` 把相同 `state={...}` 手工传入两次，只重开 EventLog；没有任何 Store/投影重建，不应计 STO-09 的产品执行覆盖。[节点](/home/uisee/zaofu_v7/tests/conformance/test_zaofu_testkit_s2.py:55)。

## 4. 可直接复用的精确注册候选

建议先补现有覆盖，再只补上述真实缺口；这些候选未在 S2/S3/S4 显式注册。按模块注册时不得再重复登记同一节点。

```text
# S2：Task CRUD/持久化、Task/Feature archive；完整文件均为有限 Store 测试
tests/test_task_store.py
tests/test_task_terminal_archive.py
tests/test_feature_terminal_archive.py
# S3：纯状态机合同；要明确不是 canonical Store 拒绝证明
tests/test_task_statemachine.py
# S2：只选 Store/锁节点，文件标记 serial，真实 multiprocessing
tests/test_state_locks.py::test_task_store_concurrent_adds_do_not_lose_updates
tests/test_state_locks.py::test_role_session_registry_concurrent_writes_preserve_all_entries
tests/test_state_locks.py::test_file_lock_optional_timeout_is_bounded
# S2：Role instance/backend 的持久化身份隔离
tests/test_role_sessions_by_instance.py::TestInstanceBasedSeeding::test_two_instances_get_different_session_ids
tests/test_role_sessions_by_instance.py::TestInstanceBasedSeeding::test_session_id_stable_across_registry_reloads
tests/test_role_sessions_backend_binding.py::TestGetOrCreateBindsBackend::test_backend_flip_rotates_uuid
tests/test_role_sessions_backend_binding.py::TestMarkBackend::test_persistence_roundtrip
# S2/S4：TaskAttempt 当前选择/读取验证；无需整文件纳入 transport 范围
tests/test_scheduler_task_attempt.py::test_current_for_task_prefers_the_only_active_run_attempt
tests/test_scheduler_task_attempt.py::test_retry_order_survives_sorted_json_reload
tests/test_scheduler_task_attempt.py::test_empty_store_keys_are_validated_fail_closed
tests/test_scheduler_task_attempt.py::test_current_scope_mismatch_is_validated_fail_closed
# S4：实际 mock transport 前 fail-closed；末项为真实 settlement API，非 run_once
tests/test_scheduler_task_attempt.py::test_corrupt_canonical_store_fails_closed_before_transport
tests/test_scheduler_task_attempt.py::test_attempt_terminal_is_monotonic_in_shadow_mode
# S2：声明输入的 projection 重建；不宣称通用 canonical replay
tests/test_i1_rebuild.py::TestTerminalIndexRebuild::test_rebuild_from_archive
tests/test_i1_rebuild.py::TestCostRebuild::test_rebuild_equals_incremental_aggregates
```

实际行为证据：[TaskStore](/home/uisee/zaofu_v7/tests/test_task_store.py:13)、[Task archive](/home/uisee/zaofu_v7/tests/test_task_terminal_archive.py:19)、[Feature archive](/home/uisee/zaofu_v7/tests/test_feature_terminal_archive.py:19)、[状态机](/home/uisee/zaofu_v7/tests/test_task_statemachine.py:10)、[多进程 Store](/home/uisee/zaofu_v7/tests/test_state_locks.py:50)、[Role instances](/home/uisee/zaofu_v7/tests/test_role_sessions_by_instance.py:23)、[backend](/home/uisee/zaofu_v7/tests/test_role_sessions_backend_binding.py:61)、[TaskAttempt 选择](/home/uisee/zaofu_v7/tests/test_scheduler_task_attempt.py:640)、[reload/损坏](/home/uisee/zaofu_v7/tests/test_scheduler_task_attempt.py:860)、[settlement](/home/uisee/zaofu_v7/tests/test_scheduler_task_attempt.py:1042)。

已注册、后续需要保留而无需重复登记的关键节点：

```text
tests/conformance/test_zaofu_testkit_s2_multiprocess.py::test_task_store_subprocess_crash_before_replace_preserves_snapshot
tests/conformance/test_zaofu_testkit_s2_multiprocess.py::test_task_store_concurrent_update_survives_one_crashing_writer
tests/conformance/test_zaofu_testkit_s2_store_crash.py::test_store_replace_crash_preserves_snapshot_and_restart_recovers
tests/conformance/test_zaofu_testkit_s2.py::test_terminal_archive_survives_index_failure_and_retries_without_loss
tests/conformance/test_zaofu_testkit_s2_surface.py::test_integrations_and_web_do_not_write_canonical_files_directly
tests/test_task_attempt_recovery.py::test_canonical_store_wins_over_conflicting_projection
tests/test_task_attempt_recovery.py::test_corrupt_canonical_store_fails_closed_instead_of_projection_fallback
tests/test_workflow_spine_projection.py::test_corrupt_projection_rebuilds_from_zero
tests/test_task_pipeline_projection.py::test_projection_replay_is_stable_and_worker_comes_from_operation
```

## 5. 最小补测出口

1. 带完整 Task contract/scope 的 Store add/get/reopen-instance → verify：独立字段/原始快照一致，不能只检查 ID/status。
2. 四类基础 Store 的 malformed syntax/错误根类型 → verify：明确拒绝、原字节保持、无低权威 fallback；再分别验证声明可重建的 index/projection 能恢复。
3. Feature/Session 的两个进程修改不同记录或独立字段 → verify：两边已确认更新均可见；保持 serial 和进程回收，不能用 sequential reopen 代替并发。
4. 授权状态入口接收旧 terminal 事件 → verify：Task/Attempt 身份和 canonical 快照不回退；另设受控 reopen 正向控制，避免禁止合法恢复。
5. 真投影删除/重建 → verify：重建前后稳定语义快照一致、canonical Store/required sidecar 字节不被当作投影删除；静态 ownership 守卫的结论限定到实际扫描范围。

不能把导入 `_RecordingTransport`、导入 Store 类、注释中的 ownership/replay 或 helper 的绿色结果，计为对应生产行为已执行。新增失败应先正式运行复现，再独立记录 Issue；本研究没有生产缺陷判定或全域通过结论。
