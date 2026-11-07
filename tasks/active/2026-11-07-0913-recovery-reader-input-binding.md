# 失败任务恢复 Reader 保留当前任务的已准入输入

> 状态: active

来源：用户持续监工/修复授权；第十八轮真实 redrive evt-137fd8746da5 正确携带部分代码，但新 Reader 在 index/recover 返回 superseded。已核实不是旧 fanout.cancelled 扩散；当前全局包 9a2c 已替换 6a47，FMS-04 合同仍绑定 6a47。input_lifecycle 的历史包例外只接受 in_progress 且 active_dispatch_id 相等的 Task Pipeline，遗漏 failed task 的活跃恢复 Reader。

## 验收

- 使用真实 WorkflowOperationService 产生 request/start，并经公开 artifact index/recover -> verify: 当前失败任务、当前合同/代次、活跃 task-domain recovery reader、摘要绑定的不可变 request/manifest/target/原包均一致时，允许原输入 pinned，仍不授权执行或重绑定。
- 更换合同、代次、任务/run、attempt/dispatch、篡改 request/manifest、终止 operation、取消本 fanout、原计划包未准入/被拒绝 -> verify: 均 fail closed；取消其他 fanout 不影响合法 reader。
- 合并 dev、重载控制面、受控恢复 -> verify: 真实新 reader 的 index/recover 与代码检查能继续，其他 worker 保持；原始失败与全部中途拒绝保留。

## 边界

只修输入投影与其真实 CLI 调用链；不变更业务验收、任务终态、合同、角色权限或 Task Map；不绕过现有凭据授权。补文档、聚焦/影响闭包/门禁/恢复 mock，提交和归档。原部分提交修复 140b1944 单独完成。


## 复现与验证

- 测试首轮缺少真实 required-read policy，1 failed/21 passed 为夹具问题。补实际 policy 后当前 dev 真正 RED：index/recover requires_rebind=true，1 failed/21 passed。
- 初版 47 passed，但实际历史回放发现 TaskStore 仍 in_progress、旧 dispatch 已清空；不能从任务聚合状态推断 operation 仍工作。改为核验 reader 存活、对应当前合同，并排除任何活跃 Impl/Verify；49 passed。
- 实际新 Reader 的 09:10 前事件回放 pinned/current_recovery_reader_binding，当前已结算 operation 仍拒绝；证据 LS docs/records/2026-11-07-recovery-reader-lifecycle-replay.json。尚待合并、加载和新 reader 实机继续。

- 自动影响闭包 15 文件 171 passed，sentinel 53 passed；额外恢复 mock/serial、Operation 与凭据边界 24 passed。补充命令首次测试文件路径误写为 tests/ 而非 tests/e2e/，无测试执行；更正后全部通过。ruff/diff 通过。
