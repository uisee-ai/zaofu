> 状态: active

# R61 当前集成阻塞的诊断派发闭环

来源：LS-v1 evt-3d760703bd03 / evt-d0213691bbcd。R60恢复实施后继续复盘。

真实历史回放：RM生成needs_diagnosis待办，但suggested_route为空，resident诊断入口不接受；fallback受其他Autoresearch单飞请求占用。原事件被泛归workflow_operation_divergent。恢复后旧block诊断仍留在队列。

- 当前 canonical needs_review→绑定 operation/request/run/task代际的RM诊断 -> verify: 实际RM tick派发已有resident入口，要求结构化recommendation，不把发送当修复完成。
- 关联generic operation.blocked不得重复诊断，精确集成被supersede/settle、新任务代际或新实施启动后撤销旧待办 -> verify: event replay和不同任务/operation隔离。
- 保留暂停、resident忙/缺失、用户待决、失败回执和诊断超时机制 -> verify: 集成诊断调用者与现有owned diagnosis测试闭包。
- 合并、受控加载并记录 -> verify: 真实FMS23已由R60恢复，不重发旧故障，不伪造新故障；实际新阻塞再观察自主诊断。

本批修复诊断输入与消费，不赋予RM跳过业务验收或任意修改状态权限，不硬编码LS脚本修复方法。产品修复建议与受控动作仍各自验证，不能把diagnosis送达记作产品恢复完成。

## 合并前验证

- 聚焦真实RM tick / resident派发、当前性与重放：20 passed。
- dev-verify plan选出382个影响文件，却按数量扩张为全tests。已终止本会话刚启动的自动全套进程（exit143，不计成功）；遵守AGENTS改为精确382文件、4并发、600s预算，不删除任何选中项：5125 passed / 7 skipped / 210.53s。
- dev-verify run --skip-tier deterministic仍执行serial10 passed、sentinels53 passed、flow mock E2E10 passed；deterministic层由上述完整影响闭包替代。未运行full pytest或真实provider测试层。
- LS-v1历史事件只读回放：原故障仅1条needs_review诊断，route=run_manager_recovery；R60恢复后的当前事件投影为0条旧集成诊断。未制造现场新故障或伪造agent回复。
- 一处测试误把终态blocked后cancel事件视为有效；canonical reducer实际忽略它，已改为断言无效cancel不能隐藏故障，未改变生命周期代码。
