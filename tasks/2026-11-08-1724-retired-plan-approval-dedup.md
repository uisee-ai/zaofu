# 退休审批不得阻塞计划恢复

> 状态: active

持续监工授权修复。FMS03 的新 ready evt-ef071da45c4d 已重新准入，但 Writer 在 evt-515cee653f5d 使用自行扫描的历史 pending 集合，把已因 plan_admission 取消的 evt-54a60ef90df1 仍当作待审批。CLI/Web 使用共享 pending_plan_requests 已正确退休它，造成无可审批请求而恢复一直等待。

- Writer 去重复用共享审批重放 -> verify: 真实 Writer 入口取消旧 ready 后允许新 ready 请求审批；尚未批准不得派发。
- 已抑制新 ready 的恢复 -> verify: 恢复扫描重新考虑新请求，幂等且不复活旧审批。
- 保留取消身份边界 -> verify: 非 kernel、非 plan_admission 或错误 causation 的取消不能退休有效审批；有效 pending 仍去重。
- 合入部署并观察原 ready 恢复 -> verify: 新审批可见且正常批准后才派发 FMS03，其他任务身份保持。

实现与验收记录补充于 [Plan 端口恢复说明](../docs/impl/product-acceptance-port-replay.md)。此项是审批生命周期投影不一致，与上一项端口语义绑定错误分开记录，不将重试等同于完成。

验证：5项新回归先2红3绿，修复后与审批身份回归共19通过；共享退休投影26通过；200文件影响闭包2971通过7跳过（92.80秒，含199 mock E2E）；串行10通过、哨兵53通过。首轮广验证遗漏PYTHONPATH被源码来源保护拦截，修正绝对路径并断言导入本worktree后重跑，不计作产品失败或通过证据。未做全pytest/真实provider E2E。
