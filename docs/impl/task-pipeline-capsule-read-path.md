# Task Pipeline 的任务胶囊读取路径

Task Pipeline派发先发布当前task capsule，再生成briefing并发送prompt。2026-10-25真实并行验收发现prompt要求按Read Order读task.md，但briefing缺少此节，worker的独立worktree也不包含项目级状态目录，导致无效搜索。

`write_task_pipeline_briefing`现在从`runtime.state_dir`通过既有task_doc路径函数生成绝对task/source/progress路径；显式核对dispatch/contract revision，读取原Controlled Artifact Inputs，最后使用同一Output Contract提交typed result。它只投影已有路径，不创建第二套合同或写canonical state。

验证与真实记录：[任务](../../tasks/done/2026-10-25-1622-pipeline-capsule-read-path.md)。

## 必读工件的有界、无损批量读取

`zf artifact read --attempt <id> --required` 先分别校验全部来源/别名的授权、
摘要与允许范围，再按工件摘要、JSON selector 和正文精确去重。小批量继续返回
JSONL；大批量不再因总输出超过 8192 字符而拒绝，返回 `required-artifact-page.v1`：
`item.content` 为本页正文，`item.aliases` 为独立授权来源，`next_cursor` 为续页游标。
使用原命令加 `--cursor <next_cursor>` 直到值为 null。每页包含 metadata/转义后的
序列化输出不超过 8192 字符；逐页消费，不把所有页重新拼为一个超长工具输出。

游标绑定当前 attempt manifest、必读声明及整批正文摘要。变更来源、变更必读范围、
旧 attempt 或越权别名拒绝交付。每个来源只登记实际返回页的范围，缺页不能满足
required-read gate。续页不是摘要；将各唯一正文的页按范围拼接，必须逐字等于
原选定切片。缓存只合并同批相同内容，不跨 attempt 伪造已读状态。

## 返工读取与预算

Task Pipeline briefing 保留当前身份、完整失败项、合同/来源入口及完成协议，
只移除与头部逐值相同的 Compact Execution Context 字段；不一致字段继续展示。
返工完成当前必读正文后，先复现失败项、检查相关代码及调用方，再覆盖合同要求的
检查；不因新 attempt 自动重复全仓扫描或全部方法包。方法全文仍物化，
`zf-yoke-dev-worker-role-context` 按实现、排错、外部事实、提交及 handoff 选择加载。

开工及心跳时运行 `zf cost --operation <id> --json`，查询运行中 operation 的
新输入、缓存读/写、输出、总 token、用量采样数、剩余额度与定价状态。
未知/部分定价时美元已用与剩余量为 null，不能解读为免费；已结束 operation
拒绝读取实时角色累积值，避免把下一任务的消耗归到旧任务。

既有 budget sweep 在 Run/Operation 的任一已知维度达到 50% / 80% 时追加
`workflow.budget.warning`，按 scope、启动/额度修订事件和水位去重，重启不重复。
这是观察事件，不启动另一个恢复 agent，也不终止任务；worker 按 briefing 固化
当前身份、版本摘要、HEAD、dirty paths、决策、全部未完 AC/返工项、完整证据引用
和下一步。100% 仍走原硬预算熔断，不扩大额度或重置累积账本。预警受用量上报
采样频率限制，不能保证在单次大调用越限前送达。

本轮不自动裁剪历史、压缩或更换 Provider 会话；这些动作不能仅凭旧读取收据
证明语义可恢复。完整原文、历史和证据保留，续跑说明帮助定位，不替代正文或
本次验收。验证范围见 `tests/test_artifact_read_batch_cli.py`、
`tests/test_task_pipeline_capsule.py`、`tests/test_workflow_budget_status.py`。
