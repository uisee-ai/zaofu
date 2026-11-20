> 状态: done — `7c38380 fix: fence reader aggregate evidence and converge exhausted operations`

# 扫描失败证据与 operation 终止一致

实际 P16B 被拒收的临时 result.json 被失败聚合重新读取并生成 gap plan；reader 已因 provider 结果缺失而终止，operation 却仍 running。

- typed blocking 聚合仅消费已接纳控制结果，未接纳临时报告不可转为 gap -> verify: 拒收报告及后续临时覆盖的反例；已接纳 rejected 语义仍可生成 gap。
- 已耗尽 reader 失败收敛对应 operation，重启也可重放 -> verify: 真实派发、终止、重复恢复；不触及新尝试、已接纳或非 reader operation。
- 聚焦闭包、sentinel、mock E2E，部署并核实 P16B -> verify: operation 不再假运行，修订计划保持待审核。

验证：先 2 failed 复现；定向 9 passed，最终影响闭包 402 passed，TaskAttempt 终止关联 7 passed，sentinel 50 passed / 1 skipped，mock flow E2E 10 passed。dev-verify planner 推导 182 文件全量层，超出本次局部修复范围；显式指定新测试解除 unmapped，实际执行上述 12 文件直接调用/合同闭包及 1 个 TaskAttempt 关联测试，未声明全量通过。

实运行：部署后 watcher 2829105，session.started evt-7462413093cc 标明 control_plane_only/workers_preserved。旧 P16B operation 被 evt-075335fd4ee0 收敛 blocked；TaskAttempt evt-8b234589ab83 同步 failed，保留原失败因果。P02 Verify 与修订 Planner 持续运行。拒收计划已由 evt-a33ec10a916d 退回，Planner 新派发 evt-ba02de959d07；本任务不声称新计划或产品验收完成。部署后 22 passed。
