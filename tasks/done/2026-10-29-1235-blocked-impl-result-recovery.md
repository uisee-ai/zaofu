> 状态: done

实现：ad7833a fix: recover admitted implementation refs after blocked run resumes

# Run 恢复后补齐已接纳 Impl 的 TaskRef 门禁

P02-CLEAN-R1 的 typed result 已接纳并 settled，但 dev.build.done 在 Run blocked 窗口被生命周期门禁拦截。恢复 active 后 v4 不走 legacy handoff，永久等待 TaskRef。

- 仅明确重新激活的 blocked Run、当前代际/已接纳且 passed 的精确 Impl 结果可重试门禁 -> verify: cancelled、仍 blocked、旧代际、其他 Run、更新 operation、未绑定结果、终态任务均拒绝。
- 重跑既有 TaskRef scope/currentness 门禁，不重做 Impl 或生成 Verify 成功 -> verify: updated/rejected 原样追加，重建进程后账本幂等。
- 原 TaskRef repair 入口接线 -> verify: 聚焦回归、sentinel、mock E2E 及实际 Verify 派发。

TDD：先 RED 2 failed / 8 passed；修复后本入口与原 repair 回归 15 passed。dev-verify 相对已验证堆叠基线 a8ad616：275 passed / 7 skipped；sentinel 50 passed / 1 skipped；flow smoke 10 passed。真实只读预检仅命中 P02-CLEAN-R1 / evt-b6c7a1f3fae0 / dadd07f。

部署入口回归 15 passed，保留 worker 控制面重启 evt-1fb26d7c0cd1。实际 TaskRef 更新 evt-bbe46aa84c21 绑定原 Impl evt-b6c7a1f3fae0 和 dadd07f；独立 Verify 派发 evt-f9351d4bb471，operation wop-verify-tp-P02-CLEAN-R1-3a6e256318f4f057-eb8e6823e8df。未伪造 Verify 结果。
