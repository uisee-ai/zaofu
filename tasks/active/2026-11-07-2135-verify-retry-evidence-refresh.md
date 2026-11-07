# R58：验证恢复允许受控刷新实施自检

> 状态: active

授权：继续监工 LS-v1、修复阻塞并记录。基于 dev 04729236 隔离实施，本地合并，不 push。

R56 修正模块验证路由后，治理旧快照只在 verification_owner/tier 两项与新快照不同，但旧自检严格绑定旧 snapshot ref/digest。只读现场预演报 impl self-check contract_snapshot_ref mismatch，直接 task-verify retry 会重陷阻塞。不能改写旧回执、伪造通过或重新规划整项产品。R57 已使真实 failed+abstained 结果可准入。

1. 增加显式 --restart-from-impl 恢复选项 -> verify: 只对已准入、当前、settled 的 failed+abstained Verify；保持旧成功Impl和失败Verify事实，新一代Impl在原工作区刷新证据，随后独立Verify。
2. 保持恢复权限和幂等 -> verify: preview只读、重复apply幂等，拒绝模式冲突、verify_only、无成功Impl、旧target/contract、活动attempt、暂停/终止、产品失败；不消耗语义返工预算，不复用旧自检冒充新证据。
3. 接入实际CLI和Reconciler -> verify: CLI→受控supersede→新Impl派发→新自检/Verify目标准入回归，原verify-only retry不变；运行影响闭包、sentinel、design199 mock E2E。
4. 现场恢复治理 -> verify: 保留2ef1f908产品提交、原工作区与全部AC；新简报task_verify/CHK-22、最终E2E仍candidate_verify；记录新operation及阶段。

验证：RED 5 failed / 27 passed；实现后88 passed，增加恢复原因投递及新快照必须新selfcheck的真实目标准入回归后107 passed。sentinel53 passed；dev-verify自动46文件闭包531 passed / 1 skipped（16.26秒），结果见/tmp/zf-r58-closure.log。未运行全仓或真实Provider E2E；现场恢复待部署，保持active。
