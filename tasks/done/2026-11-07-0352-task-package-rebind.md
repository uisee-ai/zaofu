> 状态: done

实现：`e04e3af5 fix: rebind task contracts to recovered plan packages`。

# 同路径 Task Map 的 Plan Package 重绑定

真实 LS 03:49 新包 a48ab08b / generation 6a4790a4 已准入，完成任务 2 项保留、剩余 32 项 admitted；但未完成任务的 Task Contract Snapshot 仍为旧包，三项 dispatch_deferred 明确报 package_id 与代际不一致。03:50 受控停止，不重跑 Planner。

1. 复现同 ref 新包未刷新 -> verify: backlog / in_progress 两种状态真实 TaskStore + materialization 回归失败。
2. 修复绑定判断 -> verify: 同 Run、replan、明确新包身份变化触发现有 Authority CAS；原验收保持、不重开未选择的 done、重复输入幂等；跨 Run / 非 replan / 同包不触发新增行为。
3. 闭包和部署 -> verify: 聚焦、影响闭包、sentinel/mock；合入 dev 后以 stage-retrigger(refresh_plan_bindings=true) 恢复 evt-01ff944614ea 的已准入代际，仅按冻结 Package 修复四个绑定字段，仍沿用当前完整 Plan、新包、32 项范围和历史完成。
4. 真实验证 -> verify: 出现真实 task.dispatched，合同快照和 admitted package 一致，再继续监工并记录。


已复现 dev 上 backlog / in_progress 两例失败，修正后原调用链 176 passed。
普通重触发在已准入 generation 前被幂等保护拦住，因此受控修复先核验当前代际、
包摘要、完整 map、未产生任何 stage.dispatched，再经 Authority CAS 修复绑定；
不更改任务语义或状态。已派发/已被新代际取代/缺少审批引用均拒绝。


验证：当前 dev 上 2 条正例先失败，修复后调用链 176 passed；受控动作 21 passed。
229 文件精确影响闭包 3,285 passed / 9 skipped（111.46s）；自动其余 tiers 完成，
sentinel 53、mock flow 10。测试的 superseded 反例初稿误复用了同一 trigger，修正为
真正新 trigger 后通过；不是运行时又一次故障。日志 /tmp/zf-task-package-*-20261107.*。
真实部署和 Worker 仍待验证，因此保持 active。


## 最终真实验证（UTC 2026-11-07 03:58–04:00）

- LS-v1 加载 dev 2475518b，Watcher PID 1421948；loop evt-b3fd4ad9df29，加载回执 diagnostics/runtime-load/79cde79d33ec485494e925a04ce59c84.json。
- 受控恢复 evt-83ea802b1e15 后，32 项未完成合同全部绑定当前 a48ab08b Package / 6a4790a4 generation；两项历史完成保持 done，验收不减，原 map 全 34 项保留。
- 实际 impl 派发：FMS-03-GAP-CONTRACT evt-6f2baaf3b785（03:58:52）、FMS-02-GAP-CORE evt-e12ad03d963a（03:58:58）、FMS-20-GAP-GATEWAY-02 evt-e4dbc860d145（03:59:06）；均有 workflow.operation.started / task.attempt.sent。
- 三者基线均 cc6e4d7d，包含已完成两项；各自的不可变合同快照与新包一致。必读范围为 6 项：验收、命令、共享输入、本人完整任务输入、关键 workflow/user-config。其他 33 项的正文未被列为必读；完整包保留可检索。
- 目前真实 worker 正在按 cursor 读取绑定输入，已产生本 attempt read-ledger；这是派发与上下文交接验证，不是业务任务验收完成。继续监工记录在 LS-v1 docs/records/2026-11-07-three-blockers-recovery.md。
