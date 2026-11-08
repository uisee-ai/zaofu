# 持续工具Hook不能无限跳过周期恢复

> 状态: done — `813b474a fix: bound periodic maintenance deferral under tool traffic`，已合并dev `a032c3a2`。

用户授权持续监工修复。FMS12恢复Reader在旧reader取消后仍未派发；采样发现snapshot并发失效，但这不是全部原因。代码另有确定性饥饿路径：run_once(None)在纯suppressed tool hook catch-up后直接return，连续此类批次可无限跳过全部周期恢复。先用当前HEAD真实run_once入口复现，不能把该机制直接当作现场唯一根因。

- 连续纯Hook周期 -> verify: 低开销catch-up不能无限延迟replay/recovery；最多连续跳过一个周期，下个周期必执行原有维护。
- 保留非唤醒Hook优化及durable cursor边界 -> verify: 单次仍走轻路径，callback后追加事件不提前确认；正常control batch不受影响；不放宽暂停、快照一致性、任务身份。
- 接入实际周期入口，不另建调度器或状态机 -> verify: 确定性入口测试、影响闭包、sentinel、mock E2E，提交合并后受控reload并监工记录。

参考[199](../docs/design/199-worker-failure-recovery-and-role-handoff.md)和[实现说明](../docs/impl/periodic-maintenance-under-tool-traffic.md)。

验证：真实CLI idle入口新增回归先2失败2通过；实现后相关3文件165通过。自动planner选择188个影响文件，无errors/warnings；按用户AGENTS避免仅因数量跑全套，原188文件4进程闭包2749通过（110.08秒）。自动serial10通过；额外补齐恢复变更要求的sentinel53和flow-smoke mock10，均通过。非全套pytest或真实provider E2E。

合并后入口相关12项与sentinel53通过。15:04受控重载Watcher/Web到a032c3a2；15:04复核8个原在途operation中FMS17 UI自然提交准入，其余身份/会话保持，独立Run Manager resident pane/PID也保持。旧Reader排队还受当前FMS13占用和快照一致性影响，未声称等待时间已全部消除；新逻辑的调度上限由实际CLI入口回归证明。现场记录ls-v1/docs/records/2026-11-08-maintenance-fairness/。
