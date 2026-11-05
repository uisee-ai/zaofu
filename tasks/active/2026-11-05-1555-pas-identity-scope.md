> 状态: active

# 验收规格身份字段读取范围

实时事件 evt-d235e594d944：PAS 经内核打包添加 plan_revision/task_map_generation/workflow_run_id 后，Planner 的显式 task_input_policy 未声明这些新字段，所有派发被 unassigned content 拒绝。Critic 前置检查通过但 shadow OA 在打包后正确发现此问题。

依赖：仅接纳主工作区已运行的 task_scoped_plan_inputs.py 原生 task_input_policy 支持（70 行）及其已有独立测试；这是当前故障的直接前置，不复制其他未提交修改。

- 原生 PAS 经 prepare_plan_artifact_package 打包 -> verify: Task/candidate 输入完整读取内核绑定的标量身份，不修改不可变工件内容或摘要。
- 语义字段仍由生产者分区 -> verify: 漏掉业务字段、未知 schema、对象类型伪身份继续拒绝，不把任意元数据全部放入共享。
- 真实 runtime current_plan_port_sources -> verify: 新 package 继续核对 run/generation/ref digest，再按机械身份与已有任务分区生成读取范围。

验证：实际打包回归 RED 1 failed / 12 passed；完善 required_ports 测试夹具后，输入范围、Package、继承、Artifact ledger/capability 和 writer 调用闭包 99 passed。sentinel 50 passed / 1 skipped，flow mock 10 passed。初次闭包命令含不存在的测试文件，未执行；已更正后得到上述结果。

只读生产实证：对已批准 package 36b2b573 的 36 个 PAS task 分区及 candidate 全量读取通过；两个 R2 任务的实际 current_plan_port_sources 各 12 个端口可读取，原包 SHA256 验证通过。未改运行态工件或任何任务状态。
