> 状态: active

# 按当前 Run 归属计算 goal idle 重扫

用户授权：持续监工 LS-v1，发现阻塞修复并记录。本项只修复实际观察到的历史重扫混入当前 Run 和新事件无 Run 身份问题；不取消既有有界重试规则，不批准 Q05/Q06/Q13，不改完成状态。

真实证据：2026-11-09 03:27:22 evt-f5748b7a5ff1 报 max_rescans=5/rescan_count=5；其中 evt-84a3c1a8cf4f 在 11-05 00:05 发生，当前 run.goal.started evt-34b18f08e81c 在 00:29。当前 Run 启动后的重扫实际4次。

- [x] 修复前重现混入启动前及其他 Run 的重扫/在途事实 -> verify: 当前 HEAD 回归先 RED，当前 Run 的失败上限和去重仍保持。
- [x] 复用 run_scope，按目标启动边界计算，发出的重扫/升级携带 Run 身份 -> verify: 旧单 Run 未标身份的启动后事件仍兼容，其他 Run/启动前历史不计，无可确定 Run 不发无主事件。
- [x] 覆盖实际 TickService 调用及相关恢复闭包 -> verify: 定向测试、dev-verify 影响闭包、53哨兵及相关 mock E2E；不声称全量或真实 Provider E2E。
- [ ] 记录并合 dev、受控重载，校验已完成54项不变 -> verify: 保留已有 ledger；已有错误升级只通过有审计的受控恢复处理，Owner待决不关闭，测试修复成功不冒称业务问题解决。

验证：6项真实初始RED；实际TickService入口独立RED；48定向通过；dev-verify17文件影响闭包315通过/7跳过（含确定性mock），53哨兵通过。179479事件实际前缀只读回放：旧代码错误exhausted(5)，新代码rescan ordinal5（当前4次，启动前1次排除）；未向生产账本写模拟结果。未运行全量/真实Provider测试。
