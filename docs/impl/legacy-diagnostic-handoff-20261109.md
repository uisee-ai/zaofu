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
实现 `58c2fdbb` 已合并为 `7a6a3217`，合并后定向 74 和 sentinel 53 通过。
2026-11-09 09:48 UTC 控制面 PID 533027 已加载该版本，配置及 worker 保留。

现场后续门禁仍阻塞：RM 新建议 `evt-65b0056c04fb` 通过只读绑定核对，但消费回执
`evt-02de966ccd2b` 为 deferred / `event_ledger_integrity_unknown`，未产生 OA 下游事件。
损坏位于 datal 的 `.zf-datal/events/2026-10-29.jsonl` 第 164 行、字节偏移 73141：
474 个前导 NUL 后仍有可解析的 `runtime.start.requested`，但不能据此推断损坏区原内容。
关机备份存在同一损坏；完整原件已取证复制，SHA256 为
`197cdd31fb6f1c2c657120ae352260e25118ae4c3f2d0a316b042b0cd013a742`。
取证位置：datal 的 `diagnostics/legacy-diagnosis-20261109/forensic-backup-proof.json`。
未修改 canonical ledger 或放宽完整性 gate；需可信副本或合法恢复方案后再继续此案卷。
因此代码与部署验证通过，实际 OA 收件及 P12B 恢复尚未通过；两者不能混为完成。
