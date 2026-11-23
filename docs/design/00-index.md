# ZaoFu 设计文档索引

本索引登记当前 checkout 中可访问的设计文档；不表示历史设计文档已恢复。
架构与状态所有权遵循 [AGENTS.md](../../AGENTS.md) 及当前代码、测试。

## 编号与状态

当前公开 checkout 未包含完整历史设计目录；当前已登记的设计编号到 213；201–205 已在本地设计工作中占用，不复用。
117 为历史保留编号，不复用。
后续新增前仍需核对目录、索引及历史引用，避免重号。

`candidate` 表示设计方向已记录、尚未完成实现与验收；不得据此宣称运行时已支持。
其他状态沿用仓库约定：`canonical-current`、`implemented`、`partial`、
`historical`、`superseded`。

## 当前文档路由

| 编号 | 文档 | 状态 | 摘要 |
| --- | --- | --- | --- |
| 195 | [OA/WRC Worker Runtime：借鉴 Agent Runtime 的执行合同与唤醒语义](195-oa-wrc-worker-runtime-borrowings.md) | partial | 借鉴 Agent/Tower 的 profile、worker envelope、resume、push wake、unknown gate 和 CoT 边界；第一批机械合同已接入，真实 Provider 资格仍 HOLD。 |
| 196 | [Inbox 人类审核与授权中心](196-inbox-human-decision-center.md) | implemented | 仅保留 Plan / 有效授权；问答留在原会话，运行处置移到 Operations，Workflow 提案复用原审核入口。 |
| 197 | [PRD/Spec 合同与分层阅读视图](197-prd-spec-contract-and-readable-views.md) | candidate | 对照 Spec Kit 与 OpenSpec 设计统一 PRD/Spec 正文、轻量 Runtime 合同校验、分层产物和产品/架构/开发阅读视图。 |
| 198 | [上下文索引、写后读取与恢复闭环](198-context-index-write-read-resume.md) | partial | 已接入任务输入索引、固定选择分页、恢复和消费可视化；全文搜索与实际差量交付保留后续资格条件。 |
| 199 | [Worker 失败恢复与角色交付闭环](199-worker-failure-recovery-and-role-handoff.md) | partial | 分层恢复、逐请求义务、异步诊断、作用域准入和 WRC 去重；实现证据与剩余验收见配套实施记录。 |
| 200 | [Agent 恢复职责与统一执行 Runtime](200-agent-recovery-ownership-and-unified-runtime.md) | implemented | 八个工作包完成；真实 Codex/v5 最小业务闭环，验证层级及生产资格边界见配套实施记录。 |
| 206 | [公开资料中的 Spec 方法借鉴与质量评估](206-spec-method-borrowings-and-quality-evaluation.md) | implemented（本批工程） | 六源借鉴与评分、紧凑阅读、六类真实生成/交接试点；外部原生对照、真人及完整 v5 效果资格未完成。 |
| 207 | [评审成本与增量证据优化方案](207-review-cost-and-incremental-evidence-optimization.md) | implementing | 基于真实 v5 成本与公开资料，提出输入减负、审批前/已批准增量评审、finding 收敛及交付后复核优化；含分批范围、质量约束与对照验收。 |

| 208 | [按需 Critic 的输入范围与返修收敛](208-demand-critic-input-and-convergence.md) | implementing | 修复 none/STOP，减少无损包装重复，分离首次定向与历史增量，并以真实 Channel → Plan Preview 验证。 |
| 211 | [Standalone 账号登录与云端个人工作台体验](211-standalone-login-and-cloud-workbench.md) | implemented | 定义本地默认账号、可选改密、旧 Token 迁移、云端 SSO 和登录页视觉对齐，列出两端工作台的实施与验收范围。 |
| 212 | [Workflow artifact 读取与 Agent session 恢复规格](212-workflow-artifact-reading-and-session-resume.md) | candidate | 规定冻结输入的工作区读取、严格交付证据、断点选择和会话续行，以同合同三组对照及 8×8 中断场景验收。 |
| 213 | [里程碑交接、独立验收与定时唤醒](specs/213-zaofu-antigravity-borrowings.md) | candidate | 根据 [Antigravity 调研](specs/antigravity-orchestration-review.zh-CN.md)，定义里程碑接手、验收缺口、事件与超时竞争，以及持久作业的重试和去重。 |

181–194 包含历史或本地设计编号；未纳入当前仓库追踪的文档不作为仓库文档登记。
