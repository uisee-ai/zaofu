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
| 189 | [Inbox / Tasks / Settings 与 GR8R 对齐](189-gr8r-ui-alignment.md) | implemented | GR8R 实测排版与 Geist 字体、三视图、Inbox 双栏和 General / Appearance；保留受控动作。 |
| 190 | [Channel 阶段导航与 Loop 时间线](190-channel-pipeline-and-loop-timeline.md) | implemented | 视频参考的五步导航、阶段摘要、真实比例时间线与执行详情；计划基线对比另列后续条件。 |

181–188 为历史编号，相关文档未包含在当前 checkout 中，因此不作为可访问文档登记。
