# 咨询超时通知在正式失败交接后自动退出恢复门禁

> 状态: active

用户已授权持续监工、修复阻塞并记录。现场FMS13咨询超时产生legacy human.escalate，随后worker的blocked结果准入、operation settled、kernel分诊request_replan均存在，旧通知仍阻塞RM。运维已通过精确controlled dismissal恢复当前通知；本任务补确定性收尾证明，避免重复人工介入。

- 在当前HEAD复现真实事件投影与RM恢复被旧超时通知阻挡 -> verify: 回归先红，非缺失上游/Owner决策造成的误判。
- 只对内核生成、精确请求关联的咨询超时/owner不可用通知，证明原attempt结果已准入和正式交接 -> verify: 同run/task/operation/attempt/lease匹配；迟到回答、心跳或其他任务进展不能单独放行；真实Owner门禁仍阻挡。
- 接入RM实际投影和continuation调用方，不伪造人类确认，不改事件历史 -> verify: 原事件仍可追溯，重放幂等，不授权新writer或标记业务通过。
- 影响闭包、sentinel、mock E2E通过后提交合并dev，受控重载并保留在途任务 -> verify: 记录具体测试层级和现场限制。

参考[199](../docs/design/199-worker-failure-recovery-and-role-handoff.md)和[实现说明](../docs/impl/assistance-timeout-handoff-retirement.md)。

验证：新增回归先2失败、24通过；实现后4个相关文件53通过。自动planner无errors/warnings，选择360个影响文件；其80文件阈值会改跑全套，按用户AGENTS改为原360文件4进程影响闭包：4875通过、7跳过（217.12秒），非完整pytest、非真实provider E2E。其他自动tier：serial10、sentinel53、flow-smoke10均通过。真实150323事件只读回放：修复前保留该超时阻塞、修复后只移除此通知；未写事件。当前现场已先由精确controlled dismissal恢复，14:51:27 Reader实际启动；尚不把此恢复归因为待部署新代码。
