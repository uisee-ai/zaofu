# R52补充：跨代恢复夹具与完整影响闭包

> 状态: active

监工继续核查发现 R52 的 focused 集合未覆盖两个跨代恢复直接调用者；当前 dev 真实复现21项失败，原因为共享 _two_packages 夹具声明消费选择但提供无分区 task_execution_input，早于原测试目标被新准入拒绝。

- 夹具使用真实 task-scoped-plan-input.v1，保留原先测试的语义漂移/依赖重接断言 -> verify: 两文件25项通过；不修改Runtime守卫。
- 补跑 dev-verify 选出的297个影响文件，明确路径、4进程、240秒上限 -> verify: 记录全部结果，与全仓库pytest区别；不因文件阈值扩大到完整tests目录。
- 记录遗漏及原因、合并dev -> verify: sentinel通过、source只测试变化、活跃LS不需重启。

验证结果：真实dev复现21 failed/4 passed；修正夹具后两文件25 passed；dev-verify plan/run自动层13 passed；297个明确影响文件4223 passed/9 skipped，168.80秒，4进程（排除serial/perf/host/real_provider），加先前serial10、sentinel53、mock10。未做全仓库或真实provider E2E。源改动仅测试夹具，Runtime守卫不变。
