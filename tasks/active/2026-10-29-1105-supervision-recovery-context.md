> 状态: active

# 持续监工：重派草稿身份与复用 worker 的恢复选择

用户授权持续监工，遇到无法自愈问题修复并恢复。对象 datal / workflow-0ea133e63454375e。

- S1：P12A g3/p6 的结果草稿仍绑定 p5 attempt，09:34 UTC 提交 task_context_stale。step -> verify: 重派生成当前 attempt 的草稿、旧草稿归档、同 attempt 重放保留编辑；Impl/Verify 都不复用上一 attempt 结果。
- S2：P03 已 settled 且 active_dispatch_id 被清空，但仍 in_progress/assigned_to lane-1；生命周期查询把它当成活跃任务，遮蔽新 P02-PUBLISH-CONTRACT。step -> verify: 空 dispatch 的终态 stage 不占用 worker 恢复责任，新的 running Task 正确选中，legacy 新派发不受影响。
- S3：部署后通过受控 retry/resume 恢复两任务，观察实际写入/结果提交和独立 Verify；不将只显示 Run running 当作执行进展。

现场：P02 在 10:11 UTC 达到 context 硬阈值后重启 provider，但 recovery briefing 注入失败；10:26 租约过期 suspended，直到本次监工仍未自愈。新增回归原代码 2 failed。

验证：原代码新增 2 项回归失败；修复后定向 recovery/result-submit/capsule/watchdog/mock flow 120 passed。dev-verify plan/run --base 71d6844 --tests tests/test_recovery_attempt_context.py 的 27 文件闭包 642 passed（排除 host/real_provider/serial/perf），sentinel 50 passed / 1 skipped。真实账本只读查询：旧 lifecycle 选择 P03，新 lifecycle 选择 P02-PUBLISH-CONTRACT。P02 checkpoint op-resume-afd927ff3751fd9e0c49f61d 可重派；P12A bounded retry 预览允许进入 g4。

S4（恢复中发现）：P12A 当前 g3 已超过 max_rework_attempts=2，恢复 CLI 仍返回 requested，调度器静默维持 blocked。step -> verify: CLI 预览/执行明确检查配置额度；操作员额外一次授权必须带 approval_ref，绑定原已接纳结果和恰好下一 generation；缺失/伪造/过期授权不能越过预算，正常校验层保留。本次依据用户此前“需要人工批准的，你来批准”及持续修复授权，只批准 P12A g3→g4 一次，原因是两次基础设施阻塞已有可复现修复和验证，不修改全局配置额度。

S4 验证：定向 86 passed；dev-verify plan/run --base 7725d3f --tests tests/test_operator_retry_extra_generation.py 的 21 文件闭包 196 passed，包含 8-slot mock verify 与 OA/WRC dispatch E2E；sentinel 50 passed / 1 skipped。未声明 full pytest 或完整真实产品验收。
