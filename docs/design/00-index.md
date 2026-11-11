# ZaoFu 设计文档索引

本索引登记当前 checkout 中可访问的设计文档；不表示历史设计文档已恢复。
架构与状态所有权遵循 [AGENTS.md](../../AGENTS.md) 及当前代码、测试。

## 编号与状态

当前公开 checkout 未包含完整历史设计目录；当前已登记的设计编号到 201。
117 为历史保留编号，不复用。
后续新增前仍需核对目录、索引及历史引用，避免重号。

`candidate` 表示设计方向已记录、尚未完成实现与验收；不得据此宣称运行时已支持。
其他状态沿用仓库约定：`canonical-current`、`implemented`、`partial`、
`historical`、`superseded`。

## 当前文档路由

| 编号 | 文档 | 状态 | 摘要 |
| --- | --- | --- | --- |
| 195 | [OA/WRC Worker Runtime：借鉴 Agent Runtime 的执行合同与唤醒语义](195-oa-wrc-worker-runtime-borrowings.md) | partial | 借鉴 Agent/Tower 的 profile、worker envelope、resume、push wake、unknown gate 和 CoT 边界；第一批机械合同已接入，真实 Provider 资格仍 HOLD。 |
| 196 | [Inbox 人工决策中心与阅读状态](196-inbox-human-decision-center.md) | partial | 人工决策准入、已读计数和受控动作已实施；历史兼容及来源动作仍需核对。 |
| 197 | [PRD/Spec 合同与分层阅读视图](197-prd-spec-contract-and-readable-views.md) | candidate | 对照 Spec Kit 与 OpenSpec 设计统一 PRD/Spec 正文、轻量 Runtime 合同校验、分层产物和产品/架构/开发阅读视图。 |
| 198 | [上下文索引、写后读取与恢复闭环](198-context-index-write-read-resume.md) | partial | 已接入任务输入索引、固定选择分页、恢复和消费可视化；全文搜索与实际差量交付保留后续资格条件。 |
| 199 | [Worker 失败恢复与角色交付闭环](199-worker-failure-recovery-and-role-handoff.md) | partial | 分层恢复、逐请求义务、异步诊断、作用域准入和 WRC 去重；实现证据与剩余验收见配套实施记录。 |
| 200 | [Agent 恢复职责与统一执行 Runtime](200-agent-recovery-ownership-and-unified-runtime.md) | implemented | 八个工作包完成；真实 Codex/v5 最小业务闭环，验证层级及生产资格边界见配套实施记录。 |
| 201 | [libre_discuss：用户掌握节奏的 Agent Channel](201-libre-discuss-channel.md) | implemented | 安静建群、角色邀请、主持人有界调度、前置去重及失败回退、手动 PRD 输入快照与局部取消；旧模式保留。验证边界见[实施记录](../impl/libre-discuss-implementation.md)。 |

181–194 包含历史或本地设计编号；未纳入当前仓库追踪的文档不作为仓库文档登记。
