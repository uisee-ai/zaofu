# ZaoFu 设计文档索引

本索引登记当前 checkout 中可访问的设计文档；不表示历史设计文档已恢复。
架构与状态所有权遵循 [AGENTS.md](../../AGENTS.md) 及当前代码、测试。

## 编号与状态

当前公开 checkout 未包含完整历史设计目录；现存代码、测试中的设计编号引用
已到 180，因此本次新增从 181 延续。117 为历史保留编号，不复用。
后续新增前仍需核对目录、索引及历史引用，避免重号。

`candidate` 表示设计方向已记录、尚未完成实现与验收；不得据此宣称运行时已支持。
其他状态沿用仓库约定：`canonical-current`、`implemented`、`partial`、
`historical`、`superseded`。

## 当前文档路由

| 编号 | 文档 | 状态 | 摘要 |
| --- | --- | --- | --- |
| 181 | [Channel Group 角色、完整输出与可靠接力](181-channel-group-roles-lossless-output-reliable-handoff.md) | implemented | 角色职责与可选 Skills 分离，保留现有展示及机器合同；[实施记录](../impl/channel-group-181-lossless-relay.md)列出完整输出、接力、恢复与验证边界。 |
| 182 | [Plan 完整预览、任务拆分与验收证据闭环](182-plan-review-task-slicing-and-evidence-closure.md) | implemented | 完整 Preview、分层验收与 TDD/SDD 方法已实施；洛书受控重规划及浏览器复核完成，产品 Plan 保持待审批。 |
| 183 | [运行时结果身份与恢复效果闭环补丁](183-runtime-result-authority-and-recovery-closure.md) | candidate | 派发身份、自检适配、故障分流、恢复效果、诊断环境及洛书断点续接。 |
| 184 | [验证时序与命令证据门](184-verification-stage-order-and-evidence-gates.md) | implemented | 阶段验证与依赖释放、候选命令时序、Impl 收据闭包、八槽验证及故障专用诊断。 |
| 185 | [主 Agent 路由、受控协作与 Workflow 断点恢复](185-agent-routing-delegation-and-workflow-recovery.md) | candidate | 区分 headless、单执行者、根 Agent 辅助协作与 Kernel Workflow；包含 Kimi 源码 review、Provider 能力准入、受控恢复及分期验收。 |
| 186 | [项目交付工作台：Kanban、Graph 与 Loop 关联](186-unified-delivery-kanban-graph-and-loop.md) | candidate | 复用现有投影与任务身份，统一三视图导航、依赖与覆盖展示、尝试及恢复追踪；定义只读边界、分期实施与验收。 |
