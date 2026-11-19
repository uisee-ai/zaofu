> 状态: active

# 无人验收显式启用 TaskAttempt 恢复策略

来源：混合真实轮中 TOOL-LINES / TOOL-INITIALS 未提交有效结果，租约到期后
记录 lease_expired_shadow_only。配置继承默认 shadow，未进入自动重派路径；
属于本轮验收装配遗漏，不能归因为额度，也不能声称已验证自动租约恢复。

1. 新生成的8×8验收配置显式使用既有 task_attempt.mode=enforce -> verify:
   读取实际编译配置，并用其执行租约过期，保留可恢复 expired，由 Run Manager
   产生绑定原 operation/attempt 的恢复请求。
2. 保留既有重试上限、状态所有权和沙箱权限 -> verify: 运行故障矩阵、生成器
   和8槽位 mock；不修改当前 Run 的冻结配置或既有失败结果。
3. 新轮启动前检查该策略，记录旧轮遗漏 -> verify: 新配置冻结前读取并断言，
   真实资格仍由P2持续跟踪，不以配置或mock代替真实验收。

验证：旧生成配置及真实过期路径2项RED；修复后故障矩阵/8槽位mock9通过；
dev-verify生成器及文档闭包10通过。当前Run的shadow状态保持，未补写expired状态。
