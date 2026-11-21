# Reader 失活恢复按 operation 身份隔离

> 状态: active

用户已授权隔离 8×8 发现问题后修复、验证、记录并恢复；不合并 dev、不 push。

真实 `verify-lane-2` 在原长 turn 宽限后产生 `evt-1a0351821346`，但 dispatch/trigger 为空，父任务最新派发属于 lane1，`_report_stuck_worker` 因 sibling dispatch 直接 wait。01:03 RM tick 仍未给 lane2 恢复动作。原因为父 Task 最新派发不能表示多个独立 Reader 的所有权，失活事实没有携带当前 Reader 完整身份；不把此事实误判为产品失败。

- 同父 Task 的 sibling 较新派发 -> verify: 经实际 `_report_stuck_worker` 恢复原 Reader；原 operation/request/attempt 不变，不重排父 Task。
- 信号及失败收据 -> verify: 绑定自身 operation/attempt/dispatch/run，不借用 sibling 身份。
- 生命周期边界 -> verify: 在已有 respawn lease 内重新核对 running operation、原始派发、request hash、未提交结果和 Run 准入；暂停/终态/失配/原有熔断阻止替换。
- 恢复手段 -> verify: 复用现有 provider session respawn 与 work-record/read cursor 指引；不创建新产品尝试、不清空 attempt 上限，不用 transport 成功代替验收。
- 真实观察 -> verify: 部署后 lane2 的原 operation 出现绑定恢复投递与新的必读/验收进度，最终状态另行记录。

初始 RED：2 failed / 4 passed；最终7项直接行为通过；86项调用链聚焦通过（添加terminal fanout反例前，随后增量闭包包含最终7项）；相对已验证d10ed3af自动影响闭包3317 passed/7 skipped，sentinel53 passed、flow mock10 passed。全分支dev计划已保存；未跑全pytest/coverage。真实恢复待部署签收。

聚焦验证曾检出本补丁字符串替换误及runner失败事件的2项回归；旧基线2项均通过，已修复误改并重跑86项及最终3317项闭包，未带回归部署。
