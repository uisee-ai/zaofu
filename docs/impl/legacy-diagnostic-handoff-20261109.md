# 已诊断故障的 legacy OA 转交

任务：`tasks/active/2026-11-09-0940-legacy-diagnostic-handoff.md`。
关联：[输入失败恢复](reader-failure-triage-20261109.md)。

问题不是 OA 角色缺失，而是 RM 的诊断建议 binder 仅复用自动 semantic_failure 审核筛选器。
在 v4 的现有 proposal_only 通道中，这错误地额外要求 review_blocked_tasks/autonomous_actions；
reader_execution_failure 也被当成与语义问题无关的故障直接排除，无法请求输入范围建议。

修复仍用原审核筛选器的当前任务、generation、最新已结算 Impl、准入结果、goal 和原失败身份校验。
只有绑定已有 RM 诊断请求后，才传入该请求证明的 operation 集；未启用 semantic_failure checkpoint
但已有 OA 建议角色的路线可以请求 legacy 建议。读取执行故障保留原类型，由 agent 判断输入范围，
内核不认定产品失败、不改变合同、自动执行权限或原始输入。provider/environment 故障仍走原诊断 Owner。
自动发现入口不传该集合，行为保持；已有 blocking checkpoint 也不通过本兼容路径绕过其 opt-in 条件。

转交只产生既有 proposal_only 请求。后续建议、人工批准/受控应用、新绑定与恢复效果分别核实；
新 Plan 的全局准入不代表未选中的 P12B 自动更新输入，直接 recover task-impl 也不改变输入来源。

验证：新增实入口复现初始 4 failed / 7 passed；修复后 dev-verify 影响域 1226 passed、7 skipped，
sentinel 53 passed，mock flow smoke 10 passed。新版本只读回放真实建议 evt-f969555cbd16，
保持原 request、task、operation、attempt、case 身份并形成可转交动作（配置未改）。
部署与实际 OA 收件效果待验证；不能据此标记 P12B 业务完成。
