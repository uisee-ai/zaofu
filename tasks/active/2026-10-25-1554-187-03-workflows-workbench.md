# Workflows 生命周期、模块 DAG 与流程执行工作台

> 状态: active
> 来源: [设计 187](../../docs/design/187-ai-sdlc-graph-workbench.md)；187 §2、§4、§5、§6、§7、§12；用户要求放在 Workflows。

## 实施范围

- 接入 Workflows 页面，使用通用 React Flow 画布、模块树、Inspector 和视角切换。
- 显示实际编译结构、运行中的任务/Agent、并行、汇聚、门禁、阻塞和返工；事件触发刷新。
- 支持折叠、上游/下游、搜索、范围与版本、深链接；刷新保持位置和选择。

## 验收

- [x] 从生命周期选择模块 Task 并切换流程和执行详情 -> verify: 同一身份定位、证据和尝试可查看。
- [x] 通过 API 发生任务迁移与返工 -> verify: 浏览器状态随真实事件变化，未知与历史失败保留。
- [x] 使用移动端和跨组多父图 -> verify: 无全页溢出，折叠不丢关系。

## 执行记录

- 2026-10-25：用户明确批准基于 187 创建新 worktree、生成 backlog 并实施；真实 Provider / Playwright 验证已授权。

- 验证：前端构建、模型测试与 Docker Playwright 通过；真实 Run 六视图、跨视图 Task 选择、URL 刷新、实际 Provider/model、事件刷新、桌面/移动布局均有断言。 详见 [实施记录](../../docs/impl/187-ai-sdlc-graph-workbench.md) 与 [真实 E2E 记录](../../docs/records/2026-10-25-187-workflow-graph-e2e.md)。
