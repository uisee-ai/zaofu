# Product Acceptance 输入端口的准入与重放

首次 Plan 包准入会给显式 `product_acceptance_spec` 写入系统负责的 schema、Run、Flow、plan revision 和 task map generation。原来重放直接拿未绑定或旧计划身份的原输入与绑定后的端口比较，会误判同一输入为语义修改；LS-v1 FMS03 因此在首次准入后、Writer重复准入时被取消。

`bind_product_acceptance_identity` 统一首次准入和重放时的五项身份绑定。对于显式声明 Candidate Reader 分工的端口，Writer 与已批准计划的重放都从对应 Flow 的 `stage_teams.candidate_verify` 取出分工，通过 `bind_reader_obligations` 绑定每位 Reader 的 `stage_obligation`。Writer 比较完整的 canonical JSON。输入指定了其他职责、分工缺少授权或产品验收内容与冻结包有差异时，当前代次的重放失败。

实际入口为 `admit_plan_artifact_package_for_payload`，回归直接执行首次准入和重复准入，并检查同包复用、无重复事件与语义修改拒绝。已取消的旧 ready event 不复活；修复部署后通过受控 `stage-retrigger` 发布新 ready，复用原 Planner 已完成产物，再正常审批和派发。

实施与证据：[任务](../../tasks/2026-11-08-1710-acceptance-port-replay.md)。本修复不表示产品验收或真实 Candidate E2E 通过。

现场重新准入后，发现 Writer 审批指纹去重独立扫描历史请求，未考虑准入取消与包退休，导致 CLI 已无待审批请求、Writer 却仍等待旧审批。Writer 现复用 `pending_plan_requests`，与 CLI/Web 的生命周期投影一致；已被抑制的新 ready 可由既有恢复扫描重新请求审批。有效待审批仍去重，旧取消请求不能复活，新请求仍需正常批准。回归覆盖实际 Writer 入口与恢复扫描，以及取消事件的 kernel、failure_scope 和 causation 边界。实施：[退休审批去重任务](../../tasks/2026-11-08-1724-retired-plan-approval-dedup.md)。
