> 状态: done — `83241455` fix: scope recovery diagnosis to its failed task operation

# 任务诊断续接按原故障作用域校验

承接本轮批准的 P1/P2 原故障恢复与真实验收。workflow-46d731707cd1e693 中 evt-842c261510a9 的 TASK-reverse 诊断已通过原 operation/attempt 核对，却因其他任务推进使全 Run progress_digest 变化而被静默挡下；消费记录为 consumed / controlled_action_status=blocked，没有 OA 后续请求。

## 验收

- 在绑定当前故障的诊断→OA 受控请求中使用内核冻结的 task-operation 前置条件 -> verify: 真实 Run Manager tick 在其他任务完成后仍请求同原故障 OA；原 Task 新 operation、结果/attempt/任务图变化以及 Run 终结使旧请求失效。
- 绑定诊断请求前后同一 Goal objective -> verify: 诊断期间或绑定之后目标变化均拒绝，暂停/人工/禁用/伪身份的现有反例保留。
- 保留其他 Run 级恢复前置条件 -> verify: 只识别此受控请求的 task_operation 范围，其他动作不借此获得恢复授权。

原账本只读复现：reports/真实轮 reverse-diagnosis-precondition.json，唯一差异为 progress_digest。不修改真实当前运行或旧请求。

## 验证结果

已完成实现与原账本只读回放；未加载到旧真实轮。RED 4 failed / 9 passed，专项61 passed；扩大闭包5306 passed / 7 skipped / 3 环境失败，补齐 PATH 后3项全部通过；sentinel 53 passed、流程 mock 10 passed。目标变化前后、原任务换代、暂停/人工/权限门禁保持拒绝。证据见 reports/task-scoped-diagnosis-20261118/，实现说明见 docs/impl/unattended-progress-recovery.md。
