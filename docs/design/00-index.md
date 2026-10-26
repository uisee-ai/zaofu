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
| 183 | [SDD/TDD 任务图、并行实现与持续验证演进](183-sdd-tdd-task-dag-parallel-verification-evolution.md) | candidate | 承接 182 与最新一手研究，区分已有 Task Pipeline v4、现场待验证能力和增量建议；优先验证任务级流水，完善依赖证据、并行 Verify 与持续回归，保留 Thin Judge。 |
| 184 | [借鉴 Pi 的 Intake、语义路由、阶段能力与 Task Map 设计](184-pi-inspired-intake-routing-and-agent-ownership.md) | candidate | 梳理 Intake、Kanban/Channel 与阶段能力选择；结合 Pi、Spec Kit、LLMCompiler、CodePlan、LoopsBench 等一手参考，说明 Task Map 拆分、独立验证、定向返工及对照实验。 |
| 183 | [运行时结果身份与恢复效果闭环补丁](183-runtime-result-authority-and-recovery-closure.md) | candidate | 派发身份、自检适配、故障分流、恢复效果、诊断环境及洛书断点续接。 |
| 184 | [验证时序与命令证据门](184-verification-stage-order-and-evidence-gates.md) | implemented | 阶段验证与依赖释放、候选命令时序、Impl 收据闭包、八槽验证及故障专用诊断。 |
| 185 | [主 Agent 路由、受控协作与 Workflow 断点恢复](185-agent-routing-delegation-and-workflow-recovery.md) | candidate | 区分 headless、单执行者、根 Agent 辅助协作与 Kernel Workflow；包含 Kimi 源码 review、Provider 能力准入、受控恢复及分期验收。 |
| 186 | [项目交付工作台：Kanban、Graph 与 Loop 关联](186-unified-delivery-kanban-graph-and-loop.md) | candidate | 复用现有投影与任务身份，统一三视图导航、依赖与覆盖展示、尝试及恢复追踪；定义只读边界、分期实施与验收。 |
| 187 | [借鉴 BMAD 的需求闭环、Epic 渐进细化、任务调度与 TDD](187-bmad-inspired-epic-task-map-and-tdd.md) | partial | 方法、薄 Epic 阅读层与依赖诊断已实施；[12 次真实模型对照](../records/2026-10-26-0431-187-model-effect-evaluation.md)质量基本持平，长程收益仍待验证；[实施说明](../impl/bmad-187-methods-and-validation.md)保留工程边界。 |
| 186 | [Inbox 阅读与处理、任务看板与子任务状态](186-inbox-and-task-board-human-centered-design.md) | candidate | 结合 Gr8r、Multica、Chorus 梳理 Inbox、Board／List、人工输入与执行图；保留性能实测及指标对照，按 dev 36c3bd32 修正 Workflow 现状。 |
| 188 | [Dashboard、Workflow 多 Run 与 SQLite 读模型审查补充](188-dashboard-workflow-multi-run-sqlite-review.md) | candidate | dev 36c3bd32 的 Workflow 详情竞态、列表重复归约、多 Run 准入约束、SQLite 读写／游标／checkpoint 风险及分期验收。 |
| 189 | [Inbox / Tasks / Settings 与 GR8R 对齐](189-gr8r-ui-alignment.md) | implemented | 三视图共享筛选、Inbox 双栏与 General 设置；保留受控动作和状态所有权。 |

181–188 的本地文档路由保留自主 checkout；本分支新增并纳入版本管理的是设计 189。
