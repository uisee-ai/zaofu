# R49：已由 WRC 后继承接的 Reader 必须退出恢复队列

> 状态: active

LS-v1 旧车辆诊断结果 evt-244d20b3dfbb 因旧任务已取消被正确拒绝（evt-8455e9ca5fca），但旧 Reader Operation 仍 running；provider turn 结束后又重派 retry-1（evt-a87d6ff36db2）。R43 只挡 aggregate 失败后的再规划/全局阻塞，漏掉结果准入之前的 Reader 派发恢复。当前获准后继 FMS-05-GAP-EVIDENCE-03 已实际派发，旧诊断不再拥有恢复责任。

- 复现真实运行入口 -> verify: 同 Run、同原始失败、全部 affected tasks 的显式后继均 WRC 准入时，旧 Reader 在恢复派发前取消，不能再次读取旧任务。
- 复用 R43 因果判定及既有 fanout/operation/transport 取消通路 -> verify: 不合成产品通过、不删失败、不清重试账；取消后重启重复 tick 幂等，停止只匹配原派发。
- 未批准、无 WRC、异 Run/原因、部分后继或保留旧任务不取消 -> verify: 反例维持原恢复行为。
- 集成部署并记录 -> verify: Reader/call-result/阶段恢复/Run 取消影响闭包、199 mock E2E、sentinel；真实 watcher 加载后旧诊断取消，当前 Impl 保持。

用户已授权监工中修复阻塞。实现仅修改确定性生命周期；视觉产品缺陷另经受控任务修订处理。

实现：共用同源失败→显式后继→WRC准入判据，覆盖初次派发、pending/recovery和结果处理入口，复用已有fanout/operation取消及精确provider停止。保留子trace与canonical Run分离，合并各child任务防止部分覆盖误停。

验证：修正测试fixture显式kernel来源后，原入口复现1 failed/6 passed（缺少取消）；修复后通过。初版dev-verify自动闭包700 passed、7 skipped，sentinel53 passed、flow mock10 passed；补充调用准入/取消/199 mock60 passed。加入首次派发保护后的最终闭包274 passed，新增入口反例56 passed。最终planner因引用orchestrator大模块建议201文件全量，本次遵循AGENTS按恢复域闭包验证，没有运行全pytest或宣称真实provider E2E。现场只读因果核验匹配车辆WRC evt-5596a1fe7ef2，待合并后验证实际旧Reader退出。
