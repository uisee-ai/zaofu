# ZaoFu 设计文档索引

本索引登记当前 checkout 中可访问的设计文档；不表示历史设计文档已恢复。
架构与状态所有权遵循 [AGENTS.md](../../AGENTS.md) 及当前代码、测试。

## 编号与状态

当前公开 checkout 未包含完整历史设计目录；现存代码、测试中的设计编号引用
已到 195。117 为历史保留编号，不复用。
后续新增前仍需核对目录、索引及历史引用，避免重号。

`candidate` 表示设计方向已记录、尚未完成实现与验收；不得据此宣称运行时已支持。
其他状态沿用仓库约定：`canonical-current`、`implemented`、`partial`、
`historical`、`superseded`。

## 当前文档路由

| 编号 | 文档 | 状态 | 摘要 |
| --- | --- | --- | --- |
| 195 | [OA/WRC Worker Runtime：借鉴 Agent Runtime 的执行合同与唤醒语义](195-oa-wrc-worker-runtime-borrowings.md) | partial | 借鉴 Agent/Tower 的 profile、worker envelope、resume、push wake、unknown gate 和 CoT 边界；第一批机械合同已接入，真实 Provider 资格仍 HOLD。 |

181–194 为历史或本地设计编号，相关文档未包含在当前 checkout 中，因此不作为可访问文档登记。
