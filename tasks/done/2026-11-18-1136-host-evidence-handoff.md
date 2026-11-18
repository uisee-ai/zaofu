> 状态: done

# 宿主机诊断证据接入任务接力

承接本轮批准的 P2 真实故障验收与修复。真实运行 workflow-46d731707cd1e693 的 TASK-positive_count 在宿主 Docker 已产出明确 TODO/RED 后，再次声称没有明细并阻塞。原因：task_execution_evidence 只冻结终结前 Provider 工具输出，结算后的受控 host probe 不进入接力索引。

## 范围与验收

- 将结算后、下一次交接冻结前的同 operation/attempt/合同身份宿主检查证据纳入既有受控 handoff-evidence -> verify: 实际 dispatch 交接与分页读取能访问失败输出、命令、目标提交、receipt，原生输出仍在结算处截断。
- 验证 kernel 起止事件、receipt/manifest/output 摘要和身份；历史记录只作观察 -> verify: 伪造、跨任务/attempt、损坏、未结算/未知效果不可当作证据；不宣告通过、不执行额外命令、不扩大 sandbox/宿主授权。
- 保持已冻结 handoff 不变 -> verify: 旧请求重放不偷偷追加输入；新的交接才携带新证据。

证据：真实事件 evt-e3d3f7d67945；原交接工具索引 5b9923b046a6d1618751e4193ad1f25ed26fcb916cf382df8465cd5ab2614948 未包含该事件。修改在 dev ebaa20f5 的新 worktree 完成；不修改正在验收的冻结源码。

验证：先复现2 failed/10 passed；最终调用方95 passed/2 deselected，最终影响闭包176 passed、sentinel53 passed；真实原始交接时点的隔离回放已取得TODO/exit1，未改原运行状态。实际dispatch和鉴权跨代分页读取均覆盖。实现：`4df856f8 fix: preserve post-settlement host evidence in task handoffs`。新源码完整真实无人资格仍由 P2 单独验收，本项不声称该资格通过。
