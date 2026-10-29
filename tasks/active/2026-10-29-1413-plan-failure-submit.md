> 状态: active

# Planner 执行失败的结果接纳

授权来源：持续监工，无法自愈的问题记录、修复后断点恢复。现场第二轮 Planner 缺少独立扫描证据，明确报告 execution_status=failed，却被成功 Plan 的 task_map/source_index/product_acceptance_spec 端口校验拦截，operation 未结算。

- 复现失败提交 -> verify: 原实现明确失败报告仍被候选完整性检查拒绝（RED）。
- 区分执行失败与候选语义不通过 -> verify: 真实类型化失败经过认证、身份、证据接纳并结算；普通成功或语义拒绝不能绕过 Plan 完整性门禁。
- 检查失败 schema -> verify: 空 summary / 非数组 findings 仍拒绝；artifact_production 的显式失败约定保留。
- 部署并恢复同一现场 operation -> verify: 记录真实接纳与结算事件；不伪造成功，不改 TaskStore。

验证：7 项回归覆盖失败报告与正常成功门禁；定向调用链 137 passed；dev-verify 按本补丁路径计算影响闭包（完整 --base dev 含继承的已验证批次，未重复全量套件）；premerge sentinel 50 passed, 1 skipped。现场部署待核实。
