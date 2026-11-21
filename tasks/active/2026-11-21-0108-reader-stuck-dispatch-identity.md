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

## 原生重试载体复核

1b8db87c已于01:13部署；随后补查完整账本确认，lane2于01:04:41原生Provider关闭pending_todos，01:05:04已由原有dispatch_lost路径续投，早于新补丁，不能作为本补丁真实生效证据。当前compaction仍在进行。

原生evt-62db9c24068d还暴露同一operation下两层attempt身份：外层transport TaskAttempt更新为ta-*，request_hash缺失，内层payload保留不可变Reader call/必读/WRC身份。新增回归证明1b8db87c尚不能在这种载体上完成后续respawn（1 failed/7 passed）；需从已绑定nested request取call身份、外层保留当前transport派发身份，仍校验manifest digest、当前operation和输入授权，不能把两类attempt互换。该补修还覆盖既有Reader capability renewal。

补修验证：直接行为与Reader续接22 passed；相对1b8db87c自动影响闭包3497 passed/7 skipped，sentinel53 passed、flow mock10 passed。真实载体只读preflight通过，证据reader-retry-real-preflight.json；不等于实际respawn或产品验收。

01:28:32原生lane2 idle失败，fanout以timeout/infra汇总失败(evt-def7290dea52)，Run因escalate_unresolved进入quiescent。该事件早于补修部署，不能作为补修效果证据；任务保留active，真实后继恢复效果仍待核验。
