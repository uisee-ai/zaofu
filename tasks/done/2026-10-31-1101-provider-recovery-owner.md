> 状态: done — `46b1a3c0` fix: retain run manager ownership of provider failures

# P1：Provider 拒绝后的恢复责任被错误交给 workflow

Owner 要求监工遇到问题记录、修复并继续。本次追查 Verify 拒绝未继续，复现
`ta-ef2a183ef214cf3bdaf6` 已 failed，但 `recovery_owner=workflow`、
`failure_class=workflow_operation_terminal`，Run Manager 扫描显式跳过该责任，待处理列表为空。
原终局 `evt-ed9ef04386f8` 已有正确的结构化 Provider 拒绝事实，不能删除或放行。

- Provider 非语义失败交回 Run Manager → verify: 实际 Orchestrator 入口收尾后，Run Manager 投影包含精确 attempt 的待处理事项。
- 保留不可自动重试限制 → verify: `retryable=false`、任务 blocked、operation blocked；无新 provider 调用或换模型动作。
- 修复旧错误派生字段 → verify: 常规 reconcile 按原终局证据修正 owner/class，重复执行无变化，旧事件与终局引用保留。
- 隔离无关语义失败 → verify: 缺结构化原因、类别不一致、不同终局均保持 workflow owner。
- [实现说明](../../docs/impl/provider-turn-error-recovery.md) → verify: 定向影响闭包、恢复合同、sentinel 和 mock flow。

基线 `2d6103e1`；`/tmp/zf-provider-owner-baseline-red-20261031.log` 为同一 worktree 切回原模块的复现，2 failed / 4 passed。
修复不授权重试原始拒绝、不修改任务验收结论；Provider 案件由 Run Manager 接手，产品缺陷返工属于 OA 的语义处理范围。

验证：dev-verify 自动闭包 142 passed；Provider/TaskAttempt/Run Manager 合同 241 passed；
pre-merge sentinel 51 passed；mock flow 10 passed；Ruff 与 git diff --check 通过。
