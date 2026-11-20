> 状态: active

# 已诊断输入故障向 v4 OA 建议入口的受控转交

用户授权持续监工和修复；基线 dev 263f81a6。datal 的 RM 建议 evt-f969555cbd16 被 evt-cdcc51fcfadd 以 semantic_handoff_failure_not_current_or_disabled 拒绝。配置有 OA 角色和原 proposal_only triage 通道，但 binder 只接受启用新版 semantic_failure/autonomous_actions 的自动审核候选。

- 绑定明确诊断后可请求现有 legacy OA 建议 -> verify: 实际 run_manager_tick 产生一次 orchestrator.rework.triage.requested，apply_policy=proposal_only；原案卷/任务/operation/attempt 不变。
- 自动审核与执行权限不扩大 -> verify: 无诊断时 pending_execution_reviews 仍为空；不产生 semantic checkpoint、产品返工或 worker 派发；不修改 zf.yaml。
- 当前性与权限门禁保留 -> verify: 新 operation、错误绑定、暂停、未决人工决定、无 OA 角色、provider 故障均拒绝；新版语义检查既有回归保持。
- 验证并部署恢复 -> verify: 定向测试、sentinel、mock E2E，记录真实恢复动作和效果；不能以建议已送达等同产品完成。

验证进展：4 个实入口回归已复现旧问题；影响域 1226 passed/7 skipped、sentinel 53、mock E2E 10 均通过。实现提交 `58c2fdbb`（fix: preserve legacy advisory handoff after diagnosis），合并 `7a6a3217`；合并后定向 74 和 sentinel 53 通过。09:48 UTC 控制面 PID 533027 已加载合并版本，配置及原 worker 保留。

真实恢复尚未完成：新建议 `evt-65b0056c04fb` 的只读身份绑定通过，但 `evt-02de966ccd2b` 因 `event_ledger_integrity_unknown` 延后消费，无 OA 下游事件。旧账本 `events/2026-10-29.jsonl` 第 164 行含 474 个前导 NUL，关机备份同样损坏；原文件已完整取证复制并校验摘要。不得删除异常行、绕过门禁或宣称 P12B 恢复。待可信副本或合法账本恢复完成后，复核原案卷消费、OA 收件和后续输入绑定；该现场验收仍未满足，保持 active。
