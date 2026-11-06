> 状态: done

实现：`fb28e659` — `fix: retain gap prerequisites when replacing downstream tasks`。

# 增量恢复同时替代失败任务和下游时的派发选择

用户授权解决卡点并恢复。真实事件 evt-55bf5d9b6b6a 的诊断集合含三个失败任务和十五个下游；amend 替代三个失败任务及其中一个下游，新增共享 PostgreSQL 前置。旧选择器要求 failed == superseded，导致仍选择十八项、漏掉前置，继而错误触发 workspace-root owner 检查。独立 Critic 已通过，不能靠重开已完成 F00、扩大写授权或跳过独立验证修复。

## 验收

- 合同声明的替代集合包含全部失败任务，且只包含诊断集合成员 -> verify: 恰选 amend.gap_task_ids，包含新增前置；用真实事件与 pipeline spec 重现 RED/GREEN。
- 替代集合含诊断外任务、遗漏失败任务、无诊断或显式新子集 -> verify: 不覆盖原选择；缺失增量节点仍拒绝。
- 调用链和状态保护 -> verify: writer admission/runtime、Task Pipeline orchestration、mock E2E 与 sentinel 通过；不修改原计划字节、完成合同或重试上限。
- 恢复 -> verify: 重放同版已评审工件，经过计划审批及内核准入，观察公共前置实际派发；产品通过另由 Verify/集成决定。

## 验证与运行结果

- RED：新失败/下游替代用例遗漏 PRE；真实 evt-55bf5d9b6b6a 在旧 main 重现根 owner 拒绝。
- GREEN：同一真实事件、完整 pipeline spec 恰选5项；原计划与 package 字节未变。
- 定向回归217通过（含Task Pipeline runtime-noise mock E2E）；dev-verify以本次两文件路径界定增量影响闭包，279通过；sentinel 50通过、1跳过。未运行全量pytest。`--base dev`默认计划包含此前已验证修复，未重复整分支全量闭包。
- 同版Plan复核：39任务、274 AC（旧269无缺失）、27决定，矩阵和五个任务的输入分片全部通过。
- 控制面重载 evt-f3cb422e0d9b；同版重放 evt-699722f72e62；委托审批 evt-03e35b21c5c8；代次接纳 evt-f6a11f53e373。
- 20 done保留，旧4任务受控取消/会话归档；PG前置在evt-cf9921995df4实际派发到prd-dev-lane-0，operation wop-impl-tp-F00-POSTGRES-COMPOSE-R1-f39b3-05eb99feeffc。
- 产品PG修复仍实施中，不将框架准入修复标为产品验收通过。Compose监工数据库healthy，localhost SQL读写此前通过。
