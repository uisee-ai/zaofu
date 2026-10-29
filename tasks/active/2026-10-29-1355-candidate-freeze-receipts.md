> 状态: active

# 完整 Run 跨 generation 集成收据冻结

前置 30fe6cd 已阻止增量批次提前触发全局 Verify；补充审计发现，最后完成任务若属于旧 generation，最新批次收据的 head 会落后，旧实现仍无法最终冻结。

- 冻结收集同 Run 全部当前 Task 的精确集成收据，分别按自身 generation 验证 -> verify: 两代任务，旧代最后集成，完整 candidate.ready 包含两条收据。
- 当前 package/generation 保持最新；候选版本取最后一次真实集成，各历史 head 必须是当前 head 的祖先 -> verify: head 倒退/分叉、缺收据及活跃执行拒绝，重放只冻结一次。
- 关联闭包、sentinel、mock E2E，部署 -> verify: 当前未完成 Run 仍不会冻结，正在运行的 Critic 保留。

验证：真实 git/TaskRef/CandidateRebuilder 两代任务，旧代最后集成先 RED（无 candidate.ready），修复后通过。七个正反例包括缺收据、旧代未归档/活跃执行、代际不匹配、无收据推进及分叉；关联 31 passed。dev-verify --base 8983dd3 --tests tests/test_candidate_freeze_receipts.py：128 passed；生命周期关联 26 passed；sentinel 50 passed / 1 skipped；flow mock E2E 10 passed。沿用原有收据 hydration 的祖先校验，无额外绕过。
