> 状态: active

# 增量恢复同时替代失败任务和下游时的派发选择

用户授权解决卡点并恢复。真实事件 evt-55bf5d9b6b6a 的诊断集合含三个失败任务和十五个下游；amend 替代三个失败任务及其中一个下游，新增共享 PostgreSQL 前置。旧选择器要求 failed == superseded，导致仍选择十八项、漏掉前置，继而错误触发 workspace-root owner 检查。独立 Critic 已通过，不能靠重开已完成 F00、扩大写授权或跳过独立验证修复。

## 验收

- 合同声明的替代集合包含全部失败任务，且只包含诊断集合成员 -> verify: 恰选 amend.gap_task_ids，包含新增前置；用真实事件与 pipeline spec 重现 RED/GREEN。
- 替代集合含诊断外任务、遗漏失败任务、无诊断或显式新子集 -> verify: 不覆盖原选择；缺失增量节点仍拒绝。
- 调用链和状态保护 -> verify: writer admission/runtime、Task Pipeline orchestration、mock E2E 与 sentinel 通过；不修改原计划字节、完成合同或重试上限。
- 恢复 -> verify: 重放同版已评审工件，经过计划审批及内核准入，观察公共前置实际派发；产品通过另由 Verify/集成决定。
