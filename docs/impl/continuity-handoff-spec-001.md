# ZF-SPEC-001：连续执行首批实现

本记录对应用户批准的 `docs/spec/zaofu-continuity-and-handoff.spec.md` r1，
SHA256 `f5a033210875e90fe059236974ca0c4ff2737b7b7fbbf6b7c62f6238bce8c52a`。
实施基线为 dev `0625d001`；完整覆盖与限制见
[验收对账](../../reports/continuity-handoff-20261114-review.md)。
本批是增量修复，**不表示 18 项规格或 Datal 产品验收全部完成**。

后续实现：[不可变交接与目标回执](../../reports/continuity-handoff-20261115-review.md)。
环境增量：[准备身份与安装互斥](continuity-environment-preparation.md)。
`task_pipeline_dispatch` 在冻结请求前写入 `task-handoff.v1` 并注册必读来源；
`artifact handoff-accept` 复用 operation 凭据与读取账本，事件保存接收事实；
`call_result_admission` 对新交接要求当前回执，Task Detail 展示独立接收状态。
该链路不改变 Task 完成、暂停或权限归属，也不替代旧 writer 隔离。

## 入口、责任与行为

| 改动 | 实际生产 / 消费入口 | 不授予的资格 |
| --- | --- | --- |
| 运行版本概况 | `load_receipt` 已有启动回执 → `runtime_version_projection` → `metric_views.pulse` / legacy pulse → `PulseBand` | HEAD 相同不证明脏代码相同、进程仍活着或每个 worker 已加载；任务采用明确显示 unverified |
| 当前执行的进展 | `task_evidence_signals` → Run Manager context；`CallResultAdmissionService` 补齐 kernel origin、attempt/dispatch | activity、input observation、work record、verification、admitted result 分开；准入不等于业务通过 |
| 有限输入恢复 | `build_input_recovery` → CLI required-read 分页及 next_action | 历史交付不证明本轮记忆；恢复文本不授予新权限，也不撤销有效原权限 |
| 输入与任务切分方法 | `zf-plan-execution-readiness` → 新增参考方法，三个 skill 副本同步 | 不用字符门槛裁决任务语义，不擅自把 required 改 optional |
| Setup 准备 | `TaskWorkspaceManager.prepare` / `WorkdirManager` → `run_project_setup` | 物理工作区 installer 与已接线 Provider 使用者互斥；不等于全部外部工具/共享依赖目标已隔离 |
| 正确工作区检查点 | maintenance controlled action / checkpoint → `checkpoint_root` → 当前 operation immutable request + kernel placement | snapshot 不证明源进程停止；身份损坏时不回落到旧 role checkout |
| 保守清理 | task cleanup、role retire、`zf stop` → `workspace_retention_reason` | 未知 ignored 文件、外层成果、stop 时未合入的提交保留；不据此宣称全资源图已实现 |
| 回合结束接续 | `Orchestrator.run_once` → `run_replay_sweep`，先回放结果，再 `reconcile_completed_turns` | 支持有确切 turn identity 的 Codex 和已落盘 Claude stream-json operation-v2 sent attempt |
| 同 attempt 发送 | lifecycle nudge → `DispatchContext.continuation` → authority bridge → 原 transport | 不创建新 attempt、不接管 lease、不重启缺失 worker，不绕开预算/暂停/owner/operation |
| 接续的发送边界 | `claim_completion_nudge` 在发送前持久 claim；进展投影交给 Run Manager | unknown 不是失败，也不是未发送；不盲重发；已送达不是成果已验收 |
| 工作记录推进 | `progress_nudge_state` → 校验当前 work record 的身份、正文 digest、证据 refs | 同一记录换发布事件不算新进展；记录只允许提醒，不允许结算 |
| 旧 writer 退出 | `_activate_task_stage_binding` → terminate → is_alive 确认 | 仍活着/观察失败则 `waiting_for_worker_exit`；此 adapter 观察不证明脱离父进程的外部 writer 已消失 |
| 大 Plan 阅读 | `PlanMarkdownDocument` → 共享 Markdown AST，首批 4 页，靠近底部增量 2 页，手动继续及目录定位 | 全文下载/原文仍保留；当前后端仍取得完整正文，不宣称网络按章节读取已实现 |

## 接续事件与兼容性

新增非唤醒事件 `worker.continuation.claimed`，通过 EventWriter 写入，包含当前
task、attempt、dispatch、输入读取进度及恢复责任。它是发送尝试前的持久事实，
不是第二份 Task 状态。existing `worker.stuck.recovered` 留下 prompt_injected/error；
对这条续接路径明确写 kernel origin。Run Manager 可见 unknown/delivered 与下一核查动作。

中断发生在 claim 与发送之间时保留 unknown；发生在发送后而回执前也保留 unknown。
两种情况都不能用重发猜测外部效果。新的读取覆盖或同身份的新工作记录可推进提醒边界；
重复报告、旧 attempt、暂停、未决审批/求助及在途工具不推进。下游独立结果准入照常生效。
没有新增 token/费用限制，没有新的语义裁判或第二套任务 schema。

`workflow.call.result.admitted` 增加 origin 与从已验证 envelope 提取的 attempt/dispatch，
保留原事件语义和所有既有字段；旧缺身份事实不能充当新 attempt 的新鲜进展。
metric facts 缓存版本升级以保留启动回执引用，旧缓存可重建。

## 验证与未完成范围

采用影响闭包 pytest、sentinel、Flow mock E2E、前端 build/typecheck/unit 和 Docker Playwright。
模拟 worker 的并行区间由真实子进程记录；它不证明真实模型吞吐。故障测试包括：
失去回合通知、控制器重建、发送中断、暂停/审批/求助/长工具、旧身份、重复工作记录、
setup 并发/输入改变/失败失效、错误 checkpoint 路径、ignored 成果及旧 writer 拒绝退出。

完整命令、结果及 207–209 / F01–F18 矩阵保存在验收报告。便携交接接收协议已在后续增量接入 Task Pipeline；尚需完成
受控输入修订全过程、跨 Provider 真实接力、在用环境/资源持有者全集闭环、宿主级无人监工故障、
全量人类材料目录/后端按需读取以及 Datal 原始要求的产品资格。原 Datal 暂停保持。

## 2026-11-15：Claude 回合落盘与环境使用者

后续交付见 [持续接续与资源占用报告](../../reports/continuity-runtime-20261115-review.md)。
`StreamJsonTransport` 对身份完整的 Task Pipeline 调用，通过 EventWriter 在 SDK 执行前后
直接追加 `provider.turn.opened/closed`。通知队列丢失后，idle replay 能核对当前
operation/attempt/dispatch/lease/instance/session/turn，沿用原 attempt 进行有界接续。
打开事件是观察事实，closed 沿用既有唤醒路径；两者都不代表业务完成。
无最终结果、错误/异会话结果、未决工具、未闭合回合以及暂停/旧身份不得触发接续。
异常只记录结构化原因与核查责任，不复制异常正文、prompt 或凭据。
未知外部效果仍先核对，不能通过重发猜测成功；真实跨 Provider 接管资格未在本轮授予。

## 2026-11-15：剩余规格与真实资格

用户授权承接所有未完成项形成 [ZF-SPEC-002](../spec/zaofu-continuity-remaining-qualification.spec.md)，
11 项增量映射原 F01–F18 和 207–209；Spec 与详细报告继续按本地 ignored 规则保存。
新 Spec SHA256：`e9072418b38d24182ccc65843e460983735cdbbc0efe3b7652c0555b9b106412`。
完整计划和对账：[实施计划](../../reports/continuity-qualification-20261115-plan.md)、
[真实验证与缺口报告](../../reports/continuity-qualification-20261115-review.md)。

本轮真实验证触发以下修复：

- `run_goal_completion_claim_event` 只接收正确的 artifact delivery 事件且结果正文为 passed；
  rejected/blocked/未知不先生成无效完成申请。实际 Orchestrator 实时处理和重放各有负例。
  旧终态 gate 原本已拒绝此类结果，本次不声称修复了错误完成事件。
- Generic Workflow 真实 Provider 验证入口固定身份、摘要和交付物引用，不向模型预填通过结论。
  正例修正“发货=签收”口径混淆；原错误主张保留为独立拒绝反例，不要求判断等于通过模板。
- 合成来源的 scope 与证据由 synth 实际读取并绑定准入 envelope，使 Verify 的有效引用可准入；
  跨 Run/未绑定引用仍拒绝。`--evidence-dir` 在清理前保存失败和成功输入、输出及账本，不覆盖旧证据。
- 核验方法更新 canonical `zf-evidence-synthesis` 及两份分发副本；业务语义留在方法与独立 Agent 判断。

资格证据：真实 systemd/tmux 恢复与暂停 2 场景；实际 API + Docker Chromium 16 场景，
保留 WebM/trace/截图；真实 Codex `gpt-6.1-terra` High 正反例各一次最终通过。
Provider 测试仅 Verify 为真实模型，控制面是 mock；不与宿主测试拼接成完整无人值守证明。
最终影响闭包 249 passed（Run Manager、Goal gate/receipt、准入、真实验证入口 mock、直接调用方及 skill 派发）；
sentinel 53 / Flow mock E2E 10 通过。不是全仓 pytest，也不将不同轮次重叠用例相加。

剩余范围全部保留：R01 输入修订/真实压缩；R02 全义务监督/未知效果；R03 同版视频与上传整链；
R04 持久收尾/真实语言资格；R05 服务端按需正文/目录/重连；R06 任务采用/部署；R07 跨 Provider 接管；
R08 控制意图组合；R09 真实并发/DAG；R10 SOP/历史/子工作消费；R11 Datal 原始产品资格。
B01–B07 不归档，未恢复 Datal、未 merge/push/部署。

## 2026-11-15：R05 来源正文按章读取

本批完成 R05/B07 的来源 Markdown 阅读增量，B07 继续 active。原稿完整下载保留，
来源阅读器默认只获取章节目录和当前章；目录每页最多 100 项，长章节以最多 64 KiB
UTF-8 正文片段显示，明确标注“当前未显示整章”。提供整份原文入口、章节前后切换、
分页目录、服务端正文搜索、章节书签与刷新恢复。迟到响应不替换新章节或新文档。

实际入口：`build_plan_health_router` 注册 `plan_document_routes`；
`PlanReviewReader → PlanSourceReader → PlanSourceDocument` 消费新增读取 API。
共用 `plan_preview_sources` 保留原有 owner checkpoint、task_map.ready 和恢复版本绑定，
未另造审批真源。新接口位于 `plans/{plan_id}/artifacts/{name}/document` 及
`document/sections/{section}`，必须携带来源 SHA256；目录 `offset/limit/anchor/q`、正文 `page`
均为读取参数。未知或缺失 404、越界来源 403、版本变化 409、非法 UTF-8/非 Markdown 415、
片段越界 416、超出既有 8 MiB 源文件上限 413，错误不冒充空正文或完整通过。

配置 state_dir 下 `cache/plan-documents` 保存可重建 SQLite 字节范围索引；文件身份、
大小、mtime/ctime、摘要和索引版本参与缓存身份。冷索引完整校验源文件摘要，暖读取只取
当前片段，显式全文搜索扫描有界块；缓存不存第二份正文，也不写 Agent 已读、审批或完成事件。
HTML 显式锚点、ATX/Setext 标题和 fenced code 有测试，目录不是完整 CommonMark 语义解析器。

验证覆盖实际 Web 路由、原有预览/审批/恢复调用方，以及 Docker Chromium 实际 API 交互；
详细命令、失败记录、WebM/trace/截图、源码摘要见
[本地读取验收报告](../../reports/continuity-reading-20261115-review.md)。

仍未关闭：主 Plan preview 初始整包正文、`accepted_plan` 历史兼容路径仍可能加载整份预览；
全量人类材料目录、全部 Kanban/Channel 入口、约束变更旅程、SSE 重连/删除语义仍待验收。
裸 `authority_refs` 沿用当前预览计算的版本摘要，不将其升级为历史审批时已固定的来源版本。
章节位置按当前摘要绑定，不能跨版本无条件复用。缓存清理仍属后续资源生命周期范围。
本批不触发模型、部署或 Datal 恢复；真实 Provider 后续验收按授权使用 GPT-6.1 Terra。
