# 已恢复计划准入事故的旧 Reader 收尾

> 状态: done — `6c7bff65 fix: retire readers after plan admission recovery`；已合并到 dev `9d181a64`。

监工授权：持续修复 ls-v1 阻塞，验证、提交合并并记录。真实案例：FMS03 新后继14:05已派发，13:51因旧 task_map.ready 准入失败排队的 Discovery14:17仍启动；当前 reader replacement 只识别原业务失败到后继的 rework_of，不能识别计划准入事故恢复。

- 以相同事故/原 ready 的 task_map.admitted、WRC generation 和未隔离 PlanPackage 证明恢复 -> verify: 无任一证据/异Run/异事故/异包/摘要漂移均不取消。
- 接入实际 reader 生命周期 -> verify: 排队首次派发前关闭；运行中只结束旧reader、释放operation/transport，重复tick与重启幂等，不产生业务通过或阻塞。
- 保留所有旧失败和取消事实 -> verify: 只追加既有kernel取消，禁止canonical状态直写。
- 复现、定向与影响闭包、mock E2E、sentinel -> verify: 记录确切结果后commit、merge、受控reload，只观察指定旧reader退出，业务operations不中断。

设计依据：[199](../docs/design/199-worker-failure-recovery-and-role-handoff.md)；[实现说明](../docs/impl/plan-admission-reader-closeout.md)。


验证记录：原代码在真实事件只读回放返回None；新代码解析为evt-c9b01cec6b58，未写运行事件。新增回归先出现2失败、10通过，修复后首轮4文件94通过，新增反例与真实首派发入口后本文件15通过。自动planner选择211个影响文件、无errors/warnings；其80文件阈值会扩为全套，按用户AGENTS禁止仅因文件数跑全套的规则，改为原211文件4进程定向闭包：3142通过、7跳过（118.87s），非完整pytest、非真实provider E2E。其余automatic tiers已通过：serial 10、sentinel 53（manual docs currentness ok）、flow-smoke mock E2E 10。合并后39项定向与53项哨兵通过。

现场验证：2026-11-08 14:34 UTC受控重载到9d181a64；14:35:30 kernel自动追加fanout.cancelled（evt-32dcd87babb4，superseded_by_recovered_plan_admission），14:35:34旧operation取消（evt-b74c6a6ead44）。14:40复核在途operation/attempt/dispatch/pane均保持；FMS20自然完成Impl转Verify、FMS19自然Verify拒绝转返工。只取消已恢复事故的旧reader，没有直接写业务终态。Web也已加载9d181a64。证据位于ls-v1/docs/records/2026-11-08-plan-recovery-reader/controlled-reload.json与web-reload.json。
