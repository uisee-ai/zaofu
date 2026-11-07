> 状态: active

# 授权新代际后退役旧无进展计数

LS-v1 的 stage-replan-new-generation 已成功恢复，但 Run Manager no_progress 仍累计旧代际三次 human.escalate，队首诊断等待旧 Autoresearch 请求，阻塞新计划退回的可执行动作。03:13:59 经 run_manager_tick 的既有 action_filter 成功执行 candidate-rework-apply，03:14:08 Planner 实际派发。用户已授权修复阻塞并继续监工。

1. 复现历史计数阻塞 -> verify: 新代际实际 ControlledActionService 完成后 no_progress 应清除对应旧计数，失败修复、只请求、重放或跨 Run 不能清除。
2. 保留活跃失败保护 -> verify: 新代际同指纹再次失败仍从零累计并触发阈值，其他 Run/指纹保持原状；不改事件账本、不提高重试上限。
3. 实际 RM 投影验证 -> verify: 当前候选返修自动成为 next_operation；现有诊断优先级和审批门不绕过。
4. 提交、合入、部署记录 -> verify: 影响闭包、sentinel、mock；LS 当前活跃 Planner 不为优化随意打断，下次安全边界加载并验证。

## 实现与验证

- 真实受控动作回归复现 3 类问题：成功新代际仍保留旧熔断、新失败混算旧计数、不同 Run 同指纹混算；反例校验失败动作/缺失引用/跨 Run/重放不能清除。直接测试 9 passed，包含实际 Run Manager tick 自动发出 orchestrator.replan_requested。
- 自动 dev-verify 影响闭包 1,107 passed / 7 skipped；sentinel 53 passed；mock flow 10 passed。单独 serial recovery 2 passed。不是全量测试，也不是业务产品验收。
- 首次 sentinel 发现 router 从 996 行超过结构上限，未合并；将完整计数投影移至 run_manager_no_progress.py（88 行），原 router 940 行，复跑全部自动层通过。
- LS 真实事件只读重放确认旧 no_progress 由 tripped 变为 clear；业务恢复已由同一受控 tick 成功，部署等待当前 Planner 安全完成边界。
- 日志：/tmp/zf-generation-progress-{direct,verify,serial}-20261107.log；自动回执 /tmp/zf-generation-progress-verify-20261107.json。
