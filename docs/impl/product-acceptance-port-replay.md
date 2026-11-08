# Product Acceptance 输入端口的准入与重放

首次 Plan 包准入会给显式 `product_acceptance_spec` 写入系统负责的 schema、Run、Flow、plan revision 和 task map generation。原来重放直接拿未绑定或旧计划身份的原输入与绑定后的端口比较，会误判同一输入为语义修改；LS-v1 FMS03 因此在首次准入后、Writer重复准入时被取消。

`bind_product_acceptance_spec` 统一首次准入和重放时的五项身份绑定。重放使用已经验证摘要的目标包身份；不会修改原输入、已有包或旧验证报告。所有其他字段继续按完整 canonical JSON 摘要比较，assertion、mandatory、owner、provider qualification 等任一改变仍要求新计划代次。任务图、包/Run/来源与审批退休保护不变。

实际入口为 `admit_plan_artifact_package_for_payload`，回归直接执行首次准入和重复准入，并检查同包复用、无重复事件与语义修改拒绝。已取消的旧 ready event 不复活；修复部署后通过受控 `stage-retrigger` 发布新 ready，复用原 Planner 已完成产物，再正常审批和派发。

实施与证据：[任务](../../tasks/2026-11-08-1710-acceptance-port-replay.md)。本修复不表示产品验收或真实 Candidate E2E 通过。
