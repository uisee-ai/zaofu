# 失败任务恢复 Reader 保留当前任务的已准入输入

> 状态: done
> 实现: `a1455e88 fix: retain task-bound inputs for live recovery readers`；dev 合并 `f26759c3 fix: preserve admitted inputs for task recovery readers`。

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


## 部署与真实恢复

- 合并后 49 聚焦、53 门禁通过；09:20:07 控制面 PID2492788 加载 f26759c3，runtime-load/634eb18c9d6944a98d6b994cb9fbd348.json，活动 FMS21 pane/PID 保持。
- 正式 goal active 回执 evt-726b873945e1 解除本次输入故障触发的 run blocked，TODO 09:20:51 自动派发返工 evt-0ba4491f419c。受控 stage-retrigger evt-208e0c54de3b → evt-42d1d5065a93，实际新 reader fanout evt-31d96e3247c2 / dispatch evt-ca70f2e0b6b2。
- 新 attempt run-fanout-prd-post-verify-discovery-evt-42d1d506-flow-discovery 实际 worker 调用 artifact recover 返回 bound，完整读取 3 个受控必读来源（5 页，09:23:22–09:23:46），原精确部分提交 1b3ffa8 保持；不再要求无依据重绑定。独立运行时解析同样 pinned/current_recovery_reader_binding。
- LS docs/records/2026-11-07-recovery-reader-live-binding.json、recovery-reader-lifecycle-replay.json、recovery-reader-reload-before/after.json、projects-reader-controlled-redrive.json 保存实机证明；完整操作和失败记录见 2026-11-07-three-blockers-recovery.md。
- 只完成输入恢复软件缺陷。FMS04 产品补齐、TODO 产品返工、FMS21 Q04 工程约束核对仍在进行，不记为产品 done；未 push。
