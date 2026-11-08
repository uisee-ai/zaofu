> 状态: done — 42fd6672 fix: avoid unused lifecycle event reads

# 日志捕获与用量归属查询的读取选择

用户授权持续监工、分析修复阻塞并记录。实际Watcher非阻塞栈已定位两入口；当前HEAD95a9e51d只读复现50556日窗口事件，3次_latest_dispatch_event_for_task耗时7.539秒，8次用量任务归属查询24.265秒（含冷归档解析），7次read_days复制17.618秒。FMS24/FMS12有实际归属，两个Verify lane为空。OA/RM未自动修复这一确定性查询开销，监工基于实际栈和入口测量定位。

- [x] 最新派发查询只复制两种实际消费事件 -> verify: 保持日窗口、逆序最新、顶层/payload任务ID、完整正文与追加变化；无关正文不复制，8项新测试先2RED后全过。
- [x] 无匹配任务的用量归属不读取无用日事件 -> verify: 保留active-task优先、角色名/实例匹配、任务状态、fanout终态与跨运行恢复语义；有候选时仍传完整原日事件，多run与alias反例通过。
- [x] 固定完整生产快照对比 -> verify: 全部5段174864事件，50715日事件中91派发正文/顺序精确；实际原/新查询输出相同。无profiler派发3次2.539506→0.061817秒，用量8次5.100010→0.021017秒，临时副本已清理。
- [x] 入口与直接调用/EventLog共享闭包、哨兵、mock验证 -> verify: 首轮49、自动77、最终影响闭包150、心跳夹具修正30、哨兵53、mock E2E 6项通过；非全套pytest或真实provider E2E。
- [x] 提交、合并、受控加载及记录 -> verify: dev合并d7fd54fd，合后39定向+53哨兵通过；23:13–23:14 Watcher3667506/Web3668852加载该提交，health/UI200，两条实施任务operation/attempt/dispatch/workdir/pane/进程完全保持。任务留tasks，不push。

只消除确定无用的读取/复制；不减少历史语义输入，不调整心跳阈值，不声称完整Watcher CPU问题已关闭。现场证据位于LS-v1 docs/records/2026-11-08-lifecycle-read-profile/。

扩展回归发现基线旧测试test_heartbeat_after_stuck_clears_dedup失败，dev95a9e51d单测复现：首次stuck的恢复动作创建TaskAttempt后，测试仍提交无task_id的心跳，正确被heartbeat_binding拒绝。仅补齐夹具task_id/current_task_id并断言合法心跳无拒绝，保留原去重断言及非法绑定门禁；不改生产心跳逻辑。首次闭包命令误写一个不存在测试文件，未运行测试，记录closure-invalid-path.log；更正后实际150项中149过、此夹具失败，未隐瞒为全绿。
