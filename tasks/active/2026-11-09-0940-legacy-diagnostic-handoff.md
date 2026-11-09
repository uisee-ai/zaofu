> 状态: active

# 已诊断输入故障向 v4 OA 建议入口的受控转交

用户授权持续监工和修复；基线 dev 263f81a6。datal 的 RM 建议 evt-f969555cbd16 被 evt-cdcc51fcfadd 以 semantic_handoff_failure_not_current_or_disabled 拒绝。配置有 OA 角色和原 proposal_only triage 通道，但 binder 只接受启用新版 semantic_failure/autonomous_actions 的自动审核候选。

- 绑定明确诊断后可请求现有 legacy OA 建议 -> verify: 实际 run_manager_tick 产生一次 orchestrator.rework.triage.requested，apply_policy=proposal_only；原案卷/任务/operation/attempt 不变。
- 自动审核与执行权限不扩大 -> verify: 无诊断时 pending_execution_reviews 仍为空；不产生 semantic checkpoint、产品返工或 worker 派发；不修改 zf.yaml。
- 当前性与权限门禁保留 -> verify: 新 operation、错误绑定、暂停、未决人工决定、无 OA 角色、provider 故障均拒绝；新版语义检查既有回归保持。
- 验证并部署恢复 -> verify: 定向测试、sentinel、mock E2E，记录真实恢复动作和效果；不能以建议已送达等同产品完成。

验证进展：4 个实入口回归已复现旧问题；影响域 1226 passed/7 skipped、sentinel 53、mock E2E 10 均通过。真实案卷只读重放恢复绑定通过；部署和 OA 实际收件待核验，保持 active。
