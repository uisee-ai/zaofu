# Plan 候选分区预检与实际派发一致

LS-v1 的单任务 FMS03 修订保留了 55 项任务及完整历史端口。候选预检此前遍历所有任务/端口，实际包准入却按派发任务和消费声明检查。这导致旧任务未消费的端口、未派发任务的历史分区误报，并让 Planner 错判为需要越界修订。

`evaluate_plan_candidate_preflight` 现在使用 Writer 的 `select_writer_task_items`：显式任务、wave 以及受控 successor 选择沿用现有规则。候选检查与包准入共同调用 `validate_input_body_partitions`。声明 `plan_input_selection` 时检查 implementation 与 task-verify 的并集；legacy 合同检查全局 required 与任务局部 required 的并集。

每个已提交端口仍验证完整分区结构，未消费端口的重复或畸形分区也拒绝。选中任务缺必需分区时继续要求现有固定 Run、包摘要、任务与原文继承证明；本修复不改变谱系证明、字节限制、验收映射、需求保留或审批规则。全图 schema、Goal/AC/矩阵检查仍覆盖保留任务。旧任务的 canonical 合同/输入包与在途 operation 不因候选分区检查变化而重绑。

实际入口为 Plan result validate/submit、Plan Synth 的 `evaluate_plan_candidate_preflight`；包准入为 `plan_artifact_package` 调用 `validate_dispatch_input_partitions`。`tests/test_plan_candidate_input_selection.py` 覆盖实际入口的选择、未消费历史端口、必要输入与结构拒绝；现有分区谱系、包准入和 Writer 测试覆盖后续链路。

本地实施记录：[任务与验收](../../tasks/2026-11-08-1653-candidate-input-selection.md)。真实项目只读回放记录位于 LS-v1 `docs/records/2026-11-08-candidate-input-selection/`。预检通过只证明机械准入，不证明 Planner 语义正确、独立 Verify 或真实 Candidate E2E 已完成。


## 跨计划的任务输入连续性

LS-v1 FMS24 的已准入恢复报告被 `gap_plan_amend_baseline` 拒绝：FMS24 的旧包与当前包共享正文及原始分区完全相同，其他任务的更新却改变了整个输入端口摘要。现在跨计划增量仍证明包祖先、Run、目标任务/依赖和显式消费声明；端口发生变化时，只对严格 `task-scoped-plan-input.v1` 按任务 ID 比较完整 shared 与完整目标分区。缺分区、畸形/重复分区、目标或共享正文变化均拒绝；legacy及无任务消费范围的需求权威端口仍不允许变化。

被替代任务没有同名分区时，沿固定 Task Map 的单前驱替代链找到原始输入任务。`task_scoped_plan_inputs` 的 worker 读取及候选预检共同检查沿途每个不可变端口中的原始分区；允许无关分区更新和位置变化，禁止中间目标输入变化后再还原。当前 source ref/digest 和当前 JSON 位置仍对应实际读到的当前端口，历史前驱标签与完整 shared 必读保持。不能只比较最终两份包，也不按文本相似度猜测输入等价。

实际调用验证包括 gap bridge、`validate_dispatch_input_partitions`、`current_plan_port_sources`，以及包摘要/跨Run/替代链拒绝回归。LS-v1 真实只读预检保留56项任务，53项完成记录不变，仅FMS25的下游依赖改指FMS24续接任务；Owner决定和完整AC/Goal保持。任务：[分区连续性修复](../../tasks/2026-11-09-0030-gap-scoped-continuity.md)。

## 增量任务的 owner 实例声明

Gap 序列化通过 `preserve_gap_task_declarations` 保留显式 `owner_instance` 和 `preferred_impl_role`，交由原有 Task Map 规范化及合同校验消费。此前这两项在完整 map 中合法，但经过增量任务转换后丢失，导致 PRD 后继任务的注册实例绑定消失。

`owner_role: dev` 配合注册实例沿用既有语义：materialization 将实例放入 canonical `owner_instance`，清空通用 `owner_role`；同时声明两字段时，显式 `owner_instance` 仍优先于 `preferred_impl_role`。未声明时不补值，未注册实例仍拒绝；有效实例也不使任意未注册逻辑 owner 合法。此次只保留作者原声明，不更改 assembly/bundle 同 owner 自锁 gate、lane 分配或全局配置。

`tests/test_module_gap_task_declarations.py` 覆盖真实 gap artifact writer → PRD `ingest_task_map_to_kanban` → `TaskStore` → `validate_task_contract`，校验实例、依赖、AC 和命令保留；`tests/test_task_map.py` 中既有 AVBS 自锁回归继续适用。此处验证的是机械准入与合同生成，实际计划仍须走原受控审批与派发。

## 受控恢复的显式来源索引

`candidate-rework-apply` 先通过 `materialize_explicit_plan_ports` 固定并校验作者提供的端口。提供显式 `source_index` 时，`task_map.amended`/`task_map.ready` 的 `source_index_ref` 现在使用这个已固定的原 ref，增量 writer 不再对其进行 legacy 递归替换。Writer loader 优先消费事件的 ref，随后 TaskContract 的来源索引及 evidence source_refs 指向同一正文；仅保证 Package inline 正文正确不足以证明这条引用链正确。

此前再派生会将历史 parent 中的旧任务 ID 改成新 ID，并去重原 source_refs，造成作者已审核的历史谱系变形。现修复保留整个显式正文；没有显式 producer 时仍沿用原派生行为。显式 descriptor 缺失、摘要错误或文件不存在时，继续在发布 ready/amended 前拒绝，不回退到旧索引。身份、来源、矩阵与审批 gate 不变；固定 ref 不代表业务验收完成。

`tests/test_control_actions_candidate_rework_source_index.py` 通过真实受控动作、ready 事件、`load_writer_task_map`、`Orchestrator._ensure_writer_tasks_canonical` 和 TaskStore 回读，验证正文/descriptor、历史 parent、重复来源、拒绝及 legacy 路径。任务：[显式索引恢复修复](../../tasks/done/2026-11-10-2237-explicit-source-index-recovery.md)。

## 产品交付入口的任务局部必读声明

`ingest_task_map_to_kanban` 生成 TaskContract 时，将 TaskMap 全局与任务局部 `evidence_contract.required_plan_ports` 按全局在前、局部在后的顺序取稳定并集，沿用既有 canonical 端口别名。此前全局声明会覆盖局部声明，使已经声明的必读端口在 TaskStore 中丢失。没有全局声明时继续保留原局部声明；不补充作者未声明的端口，不修改源 TaskMap 或 `plan_input_selection`。

原 TaskMap validator 仍在 materialization 前拒绝非法名称、同一声明内的重复端口及未绑定的选择。`current_plan_port_sources` 仍先校验所有必读端口已被不可变 Package 绑定，再按 implementation 或 task-verify 的原选择读取；一个已声明的局部必读端口即使未被该 profile 选中，真实绑定缺失也继续拒绝。

`tests/test_product_required_inputs.py` 覆盖实际 ingest → TaskStore → 输入读取、并集/别名、全局或局部单独声明、legacy 无声明及上述拒绝路径。本修复针对独立 product-delivery 入口；LS 当前 writer 路线原本已保留局部声明，没有变更其代码或运行态。原始 R2 全图在该独立入口另有 `scope` 完整性拒绝，未在本批修改，因此这里的临时状态回归不代表 R2 全图经该入口成功或实际 LS 恢复完成。
