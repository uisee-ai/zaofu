# Plan 候选分区预检与实际派发一致

LS-v1 的单任务 FMS03 修订保留了 55 项任务及完整历史端口。候选预检此前遍历所有任务/端口，实际包准入却按派发任务和消费声明检查。这导致旧任务未消费的端口、未派发任务的历史分区误报，并让 Planner 错判为需要越界修订。

`evaluate_plan_candidate_preflight` 现在使用 Writer 的 `select_writer_task_items`：显式任务、wave 以及受控 successor 选择沿用现有规则。候选检查与包准入共同调用 `validate_input_body_partitions`。声明 `plan_input_selection` 时检查 implementation 与 task-verify 的并集；legacy 合同检查全局 required 与任务局部 required 的并集。

每个已提交端口仍验证完整分区结构，未消费端口的重复或畸形分区也拒绝。选中任务缺必需分区时继续要求现有固定 Run、包摘要、任务与原文继承证明；本修复不改变谱系证明、字节限制、验收映射、需求保留或审批规则。全图 schema、Goal/AC/矩阵检查仍覆盖保留任务。旧任务的 canonical 合同/输入包与在途 operation 不因候选分区检查变化而重绑。

实际入口为 Plan result validate/submit、Plan Synth 的 `evaluate_plan_candidate_preflight`；包准入为 `plan_artifact_package` 调用 `validate_dispatch_input_partitions`。`tests/test_plan_candidate_input_selection.py` 覆盖实际入口的选择、未消费历史端口、必要输入与结构拒绝；现有分区谱系、包准入和 Writer 测试覆盖后续链路。

本地实施记录：[任务与验收](../../tasks/2026-11-08-1653-candidate-input-selection.md)。真实项目只读回放记录位于 LS-v1 `docs/records/2026-11-08-candidate-input-selection/`。预检通过只证明机械准入，不证明 Planner 语义正确、独立 Verify 或真实 Candidate E2E 已完成。
