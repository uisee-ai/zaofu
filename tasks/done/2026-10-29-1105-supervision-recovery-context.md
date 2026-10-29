> 状态: done

# 持续监工：重派草稿身份与复用 worker 的恢复选择

用户授权持续监工，遇到无法自愈问题修复并恢复。对象 datal / workflow-0ea133e63454375e。

- S1：P12A g3/p6 的结果草稿仍绑定 p5 attempt，09:34 UTC 提交 task_context_stale。step -> verify: 重派生成当前 attempt 的草稿、旧草稿归档、同 attempt 重放保留编辑；Impl/Verify 都不复用上一 attempt 结果。
- S2：P03 已 settled 且 active_dispatch_id 被清空，但仍 in_progress/assigned_to lane-1；生命周期查询把它当成活跃任务，遮蔽新 P02-PUBLISH-CONTRACT。step -> verify: 空 dispatch 的终态 stage 不占用 worker 恢复责任，新的 running Task 正确选中，legacy 新派发不受影响。
- S3：部署后通过受控 retry/resume 恢复两任务，观察实际写入/结果提交和独立 Verify；不将只显示 Run running 当作执行进展。

现场：P02 在 10:11 UTC 达到 context 硬阈值后重启 provider，但 recovery briefing 注入失败；10:26 租约过期 suspended，直到本次监工仍未自愈。新增回归原代码 2 failed。

验证：原代码新增 2 项回归失败；修复后定向 recovery/result-submit/capsule/watchdog/mock flow 120 passed。dev-verify plan/run --base 71d6844 --tests tests/test_recovery_attempt_context.py 的 27 文件闭包 642 passed（排除 host/real_provider/serial/perf），sentinel 50 passed / 1 skipped。真实账本只读查询：旧 lifecycle 选择 P03，新 lifecycle 选择 P02-PUBLISH-CONTRACT。P02 checkpoint op-resume-afd927ff3751fd9e0c49f61d 可重派；P12A bounded retry 预览允许进入 g4。

S4（恢复中发现）：P12A 当前 g3 已超过 max_rework_attempts=2，恢复 CLI 仍返回 requested，调度器静默维持 blocked。step -> verify: CLI 预览/执行明确检查配置额度；操作员额外一次授权必须带 approval_ref，绑定原已接纳结果和恰好下一 generation；缺失/伪造/过期授权不能越过预算，正常校验层保留。本次依据用户此前“需要人工批准的，你来批准”及持续修复授权，只批准 P12A g3→g4 一次，原因是两次基础设施阻塞已有可复现修复和验证，不修改全局配置额度。

S4 验证：定向 86 passed；dev-verify plan/run --base 7725d3f --tests tests/test_operator_retry_extra_generation.py 的 21 文件闭包 196 passed，包含 8-slot mock verify 与 OA/WRC dispatch E2E；sentinel 50 passed / 1 skipped。未声明 full pytest 或完整真实产品验收。

## 修复批次闭环（持续监工仍继续）

- 7725d3f fix: bind recovery drafts and worker ownership to current attempts
- db85b82 fix: make exhausted implementation recovery explicit and bounded

部署目录回归第一批 60 passed，第二批 62 passed。主运行目录保留其余 dirty 修改；source worktree 提交仅本轮路径，无 merge/push。

11:15 UTC：P02-PUBLISH-CONTRACT 在 HEAD 549474dc538d45403c9ff6367b1e09a148ef6f74 上 smoke 1/runtime 57/browser 2 通过，结果 evt-0fa231bf2d28 获内核接纳，TaskRef 更新与 impl.self_check.completed 已发生。11:18 UTC：P02 Verify 经 checkpoint op-resume-1879a795f4f6156c59fad62e 恢复至 p2（evt-8b9c56e548d3），绑定 attempt ta-bdb7609868ca216e3a5b。

P12A 的额外一次授权 evt-5451f86a8af7 明确绑定 g3 的已接纳 blocked 结果及 g4；不改全局额度。11:18 UTC 已派发 g4/p7（evt-0b8cdd7038e7），operation wop-impl-tp-P12A-745b3240199676ab-impl-re-8b2676fc0298；scratch marker 和 self_check.attempt_id 均为 ta-42f8c07c02503c9c1713。watcher 2228537 ready，两个阶段并行。

本条 done 只表示本轮恢复缺陷的实现、验证和部署完成。独立 Verify、集成与整个产品交付尚在运行，持续监工继续；不得把 worker 自测当作产品已验收。
