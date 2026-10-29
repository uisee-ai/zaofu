> 状态: active

# 扫描失败证据与 operation 终止一致

实际 P16B 被拒收的临时 result.json 被失败聚合重新读取并生成 gap plan；reader 已因 provider 结果缺失而终止，operation 却仍 running。

- typed blocking 聚合仅消费已接纳控制结果，未接纳临时报告不可转为 gap -> verify: 拒收报告及后续临时覆盖的反例；已接纳 rejected 语义仍可生成 gap。
- 已耗尽 reader 失败收敛对应 operation，重启也可重放 -> verify: 真实派发、终止、重复恢复；不触及新尝试、已接纳或非 reader operation。
- 聚焦闭包、sentinel、mock E2E，部署并核实 P16B -> verify: operation 不再假运行，修订计划保持待审核。

验证：先 2 failed 复现；定向 9 passed，最终影响闭包 402 passed，TaskAttempt 终止关联 7 passed，sentinel 50 passed / 1 skipped，mock flow E2E 10 passed。dev-verify planner 推导 182 文件全量层，超出本次局部修复范围；显式指定新测试解除 unmapped，实际执行上述 12 文件直接调用/合同闭包及 1 个 TaskAttempt 关联测试，未声明全量通过。
