> 状态: done
> 实现: `7de42409` — chore: enforce attempt recovery in unattended qualification fixtures

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

已合入dev `fadf730b`。新隔离轮 /tmp/zf-eight-enforce-20261119T034022Z
在尚未启动时读取实际编译配置：enforce、max_attempts=3、8/8及16 active，
Plan仍需批准，浏览器脚本摘要保持。新的driver在受控workflow-submit前再次断言enforce。
证据：reports/unattended-attempt-policy-20261119/next-run-compiled-policy-preflight.json。
真实完整资格仍由P2跟踪，本任务完成仅指验收配置及其恢复路径mock修正。
