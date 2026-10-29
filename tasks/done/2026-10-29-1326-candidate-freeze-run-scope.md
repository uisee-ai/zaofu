> 状态: done — `30fe6cd fix: prevent delta generations from freezing unfinished runs`

# 增量 generation 不得绕过同 Run 未完成任务冻结候选

实际 candidate.ready evt-f78365412691 只检查新 generation 的 P02 两任务，忽略继承 generation 的 P03/P16B 与 20 个 backlog，触发八路完整候选 Verify。此时正式新计划仍修订中。

- 冻结前检查同 Run 所有当前 task generation context 的完成或受控替代状态 -> verify: 新批次 done、旧任务 backlog/in_progress/未授权 cancelled 均不冻结；其他 Run 不阻塞。
- 收敛已经误派的候选 Verify -> verify: 仅关闭由 candidate-freeze-receipt 触发且同 Run 存在未完成任务的活动 fanout，保留 Planner 和其他 Run；用既有取消、operation、worker 生命周期路径。
- 聚焦回归、sentinel、mock E2E，部署并核实 -> verify: 无新增错误 candidate.ready，八路误派终止，规划持续。

验证：4 个 RED 复现跨 generation/Run 缺陷；8 个 scope 用例及既有真实冻结、reader 取消路径覆盖通过。定向 92 passed；dev-verify --base a31b961 --tests tests/test_candidate_freeze_run_scope.py 自动闭包 121 passed（含八槽 Verify mock E2E）；sentinel 50 passed / 1 skipped；flow mock E2E 10 passed。真实状态只读预演：38 个 managed task contexts，仅匹配 fanout-prd-lanes-verify-evt-f7836541 的 8 个 children，不匹配 Planner。

实运行：watcher 3120775；session.started evt-72a3544844ac 保留正在运行的 worker。fanout.cancelled evt-f6820eb93eee 以 candidate_freeze_incomplete_run_scope 收敛唯一误派 fanout；8 个 operation 全部 cancelled，Planner 同一 operation 持续 running。主运行目录部署后 24 passed。候选历史收据与已完成任务保留；不宣告新计划批准或剩余任务完成。
