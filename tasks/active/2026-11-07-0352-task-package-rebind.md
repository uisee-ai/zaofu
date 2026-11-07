> 状态: active

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
