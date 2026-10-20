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
| 182 | [Plan 完整预览、任务拆分与验收证据闭环](182-plan-review-task-slicing-and-evidence-closure.md) | partial | 以洛书 FMS 为案例，评审 Preview 引用丢失、分层完成契约、任务粒度及 TDD/SDD 证据，提出待批准的 backlog 与验证顺序。 |
