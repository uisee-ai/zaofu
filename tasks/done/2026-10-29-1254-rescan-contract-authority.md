> 状态: done — 2922d2a fix: bind rescan templates and results to scoped plan authority

# 全局重扫描派发、模板与结果权威一致

P16B 请求及派发已绑定新 Plan，但模板通过旧 Task snapshot 生成旧 generation；结果门禁又按 Task 当前合同检查。报告 evt-0e53132a36fa 被 stale_call_result_superseded 拒收。后续 provider terminal 恢复还沿用该报告生成待审批 gap plan，须复核来源后再批准。

- 全局扫描使用不可变 rescan-scoped task-contract-snapshot.v1，绑定当前 Plan/package，保留原 Task snapshot ref/digest；不重写 TaskStore -> verify: briefing 模板、派发与结果 generation 一致。
- gap attempt domain 与扫描权威校验；原合同、Plan、目标或代际变化仍拒绝 -> verify: 完整 Orchestrator dispatch→result admission/settlement，含真实 canonical Task 和反例。
- 回归验证并部署，恢复实际扫描、复核待审批计划来源 -> verify: 接纳结果后方可继续审批，不把旧拒收报告冒充已接纳证据。

验证：端到端先 RED（snapshot generation-1 对比 current generation-2）；修复及六类正反例、原输入不变/重入幂等共 7 passed。聚焦 202 passed；dev-verify 基线 cdb9131 影响闭包 533 passed；sentinel 50 passed / 1 skipped；补强源 Run/Task 校验后的最终关联回归与 stale-contract mock E2E 203 passed。

现场验证：2026-10-29 14:53 UTC，新扫描 evt-52bf59f9540a 已接纳 evt-fe5141944813、operation settled evt-261a3ee78dcb。contract_revision=rescan-contract-r583644db681e5164fb3e，generation=96b9407094a6c817974dab2a28d458b9f29f603e3a56d9ce01a66864dd74ad40，candidate=4c4abcb73a88e105cdae6083ee590aec1367511e；扫描语义 rejected，确认两项既有未实现职责，不冒充产品通过。旧被拒收扫描及中途因 Run blocked 取消的尝试仍为历史诊断。后续阶段上限单独处理，不改变本修复的合同接纳验证结论。
