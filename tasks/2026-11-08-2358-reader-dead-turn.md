> 状态: active

# Reader 旧进程退出后的未闭合回合阻塞恢复

用户授权持续监工、修复并记录。dev ecd83c18 现场复现：FMS24 blocked 后 RM 发出恢复请求 evt-c15ed6d2ca56，reader fanout evt-cee6015d2d8d 已创建，却连续因 reader_context_switch_waiting_for_settlement:provider_turn_active 延迟；同时 worker idle、transport_alive=false，tmux 无 flow-discovery 进程。准备上下文先检查历史回合，未在旧进程已退出时释放该单独条件。不得清除活跃 child/task，也不得将未知存活状态视为已退出。

- [x] 实际 dispatchability 入口复现 -> verify: 未闭合历史 prompt + 已退出旧 reader + 旧 child 已终结，复现不派发；保留原事件。
- [x] 最小恢复修复 -> verify: 只有确定旧 transport 退出且其他原保护条件通过才走既有轮换、用量收尾与重启；活跃/未知进程、活跃 child/task、busy 继续等待。
- [x] 验证与文档 -> verify: 定向影响闭包、哨兵及 mock E2E；不声称 full 或真实 provider 全面验证。
- [ ] 提交合并受控加载 -> verify: FMS12 Verify 身份/进程保持，FMS24 reader 实际启动并绑定原恢复请求；记录 OA/RM 为何未自动解决。

现场证据：LS-v1 docs/records/2026-11-08-reader-dead-turn/。不批准 Q06，不改变 FMS24 AC，不重放部分成果为已完成。

复现与首轮：7项新回归先2RED/5GREEN。首次补丁测试60通过/1失败来自新夹具把活跃Task沿用了已完成fanout的ID；既有实现正确将该任务视为终结，改用独立OTHER活跃任务后214项定向闭包通过。未因此改生产任务保护。真实176212事件只读重建确认旧turn始于15:26:58，约8.5小时；无running reader operation、无pane。正式自动检查411通过/7跳过、53哨兵、10 mock E2E通过；不是full pytest或真实provider全面验收。
