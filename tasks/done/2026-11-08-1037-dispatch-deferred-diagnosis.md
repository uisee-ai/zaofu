# Task Pipeline 重复派发异常进入诊断闭环

> 状态: done；实现 `8d9e0127` — `fix: route repeated pipeline dispatch failures to diagnosis`，dev合并 `2a21642e`。

持续监工LS-v1真实evt-aa13d2fd494f与causal cooldown evt-c0e5bd95d219，当前HEAD Supervisor无对应attention；source event未注册问题分类。前项基线修复已经恢复任务，本项修复自动诊断入口遗漏，不伪造worker失败、不跳过Git证明、不启动全局重规划。用户已授权遇到问题修复并记录。

- [x] 真实事件重放及旧HEAD回归先复现 -> verify: 同scope重复机械派发失败无诊断事项。
- [x] 复用事件问题注册策略，Kernel冷却证据绑定当前Run/task/generation/stage -> verify: 单次失败/普通waiting/伪造来源/跨任务与跨Run均不触发；同故障幂等。
- [x] Supervisor及RM实际入口均消费；迟到快照不复活已恢复异常 -> verify: 真实派发/新代次准入后退休，心跳/其他任务进度不能清除；生成已有diagnose-attention action。
- [x] 聚焦影响闭包、sentinel、mock E2E，通过后提交归档合并dev并受控加载 -> verify: 实际LS已恢复故障不再诊断，保留在途worker，真实新故障自然触发待观察。

参考docs/design/199-worker-failure-recovery-and-role-handoff.md；实现说明docs/impl/task-pipeline-dispatch-diagnosis.md。证据LS docs/records/2026-11-08-dispatch-deferred-diagnosis/。不push。

## 验证记录

新回归先9fail/10pass；补Supervisor超过500事件的入口及旧代次/迟到冷却后，聚焦91pass。真实LS阻塞切片产生1 attention+1 diagnose-attention，实际恢复后当前账本与陈旧快照均0待诊断；未执行真实诊断动作。

规划器因385测试文件>80自动扩大为全套；已主动中止自己的进程(exit130及xdist teardown告警)，不是产品失败。按规划器明确selected_tests执行385文件影响闭包，5163passed/7skipped；串行10、sentinel53、mock flow10；额外flow/design199 E2E4通过。未完整pytest/真实Provider E2E。日志/tmp/zf-dispatch-diagnosis-{red,focused,impact,gates,mock}.log，plan与gate receipt同前缀。完整记录包括失败和验证边界。

## 部署与边界

合并后43聚焦+53sentinel通过。10:45:31–10:46:23受控control-plane-only重载watcher1802374→1861996，加载2a21642e；在途FMS14-03 operation/attempt/dispatch/pane%1688/pid1803271完全相同，worker已完成读取并进入基线测试。Dashboard10:46:46 PID1783890→1864902，加载2a21642e，health/页面HTTP200，原环境保留且未复制授权URL。

当前真实账本pending dispatch failures为空，Supervisor无本类open attention；旧故障未被重新激活。新故障自然发生后的RM agent实际输出仍待观察，本项done指代码/回放/入口测试和已部署，不宣称自动修复未知问题。记录LS docs/records/2026-11-08-dispatch-deferred-diagnosis/{controlled-reload,web-reload,deployed-current-recheck}.json。
