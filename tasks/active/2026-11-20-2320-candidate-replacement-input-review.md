> 状态: active

# Candidate 替代任务缺少输入消费审核时转交语义规划

8×8 窗口6复现：16项Task已完成，Candidate mixed failure 的自动恢复从路径所有者拼出替代任务，却遗漏 predecessor 的 plan_input_selection，随后准入拒绝，控制面无有效后继。用户已授权修复并恢复；本任务限定隔离验证分支，不合dev、不push，不增加attempt上限。

- 复现现有生成器及Run Manager调用 -> verify: 有消费合同的终态所有者，不得生成省略消费合同的替代任务；当前HEAD新增回归先失败。
- 保留已有replacement准入门，使用既有semantic replan路由 -> verify: 没有明确继任审核时转交Agent，绑定原失败、当前候选及合同；不复制旧selection冒充新判断，不把宿主诊断路径直接变成写权限。
- 保留兼容行为 -> verify: 无消费合同的既有路径恢复测试不变；明确gap计划仍由原准入验证，已完成Task不直接重开。
- 回归及真实接续 -> verify: 影响闭包、sentinel、恢复mock通过；受控部署后记录实际后继派发。最终产品和无人资格独立判定。

复现：原HEAD新增调用入口回归1 failed。修复后定向174 passed；最终影响闭包1652 passed/7 skipped、sentinel53 passed、flow-smoke10 passed。原准入selection否定测试保留，显式gap不再被自动拼装覆盖。真实账本只读预演将evt-1adc0a84bf49路由为candidate_replan，保留87e7代次及09094c2d候选，无自动gap。待部署后核实实际派发。
