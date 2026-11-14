> 状态: active

# Gap 计划输入分区补齐计划身份字段

P03 的真实 gap 结果已接纳，但 gap-plan 在产品验收输入端口因 `$.plan_revision` 未分配而失败；旧 partition 只覆盖业务摘要，后继计划新增 workflow/run identity。运行时不能把该结构问题算作产品实现失败，也不能扩大读取范围。

- gap rebind 对继承 port -> verify: 仅把 workflow_run_id/plan_revision/task_map_generation 等已存在的顶层身份字段加入 shared_paths，保留 task-specific partition。
- 普通完整计划和错误/缺失分区仍 fail-closed -> verify: 非 gap 任务不自动扩权；空/无来源 gap 继续拒绝。
- 真实 P03 断点恢复 -> verify: 同一候选的 gap plan 可通过 package admission，再由人工/Planner 处理真实缺口；不宣称 align 产品通过。

验证：先 RED（P03 gap package rejected at $.plan_revision），修复后 251 passed，sentinel 50 passed/1 skipped，Flow smoke 10 passed。部署现场恢复待核实。
