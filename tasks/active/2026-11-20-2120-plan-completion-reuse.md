> 状态: active

# 已审阅 Plan 恢复时复用已集成 Task 完成证据

真实 8×8 实验中新 Plan 只更新恢复基线及原始有限数值边界说明，原 16 项已完成；批准后却重新派发 8 Verify 和 4 Impl。OA 的保留完成意见未成为运行时可消费的证据。pre_impl 为 shadow，此处不是门禁绕过。

授权沿用用户“遇到问题修复，修复后评估并从断点继续”；限独立验证工作树，不合并主线、不 push。

- 精确版本人工/Owner 复用审核绑定当前 Plan、候选、每项新旧合同与原 native Verify/integration -> verify: 缺失、篡改、运行中、目标移动、过期审批拒绝且不修改 Task。
- 现有 stage-retrigger 受控路径接入完成复用；TaskStore CAS，事件记录引用原完成，无新造 worker passed -> verify: 整批预检、重复调用和 CAS 中断恢复。
- Run frontier 保留各 Task 原 generation，Candidate authority 核对复用证明与当前合同 -> verify: 新 Candidate 使用新 Plan，原 Task 证明不改写、不冒充新执行。
- 已启动但正常停止的 attempt 仅在终止且工作树无未审阅改动时允许恢复 -> verify: 活动 attempt 和修改过的工作树拒绝。
- 修复后影响闭包、sentinel 和 mock；受控部署并恢复真实验证 -> verify: 记录 native Candidate 后续阶段；无人原窗口失败保留，原 8×8 不重复计数。

这是一条显式审阅的恢复路径，不以相同 ID、文件数量或运行成功推断语义等价；自动语义复用尚需 OA 提交同等绑定的决定，不能宣称本修复证明零介入。

实现已接入 `stage-retrigger`、Task Pipeline frontier、Candidate 冻结及独立验证权威。原部署 `69b2a6e1` 的同一行为回归失败（旧入口忽略 dry-run 复用参数并发出普通重触发），新实现通过。

验证：定向 76 passed；相对已验证 `f5113bd0` 的自动影响闭包 5611 passed / 9 skipped（257.93s），sentinel 53 passed，flow-smoke 10 passed。未运行全量 pytest、coverage；真实复用/后续 Candidate 待部署观察。原部署与修复分支分别运行回归，保留真实 RED 日志；错误工作树的 import guard 拒绝不记为产品 RED。
