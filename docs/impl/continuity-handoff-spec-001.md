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

## 2026-11-15：按成功条件推进的剩余关闭清单

用户批准按完整复盘的修正顺序执行；沿用 ZF-SPEC-002 与 B01–B07。
本表为验收索引，不是第二套 runtime Task 状态。`待关闭` 不等于全部从零实现。
每条关闭必须补实现调用方、失败/负例、适用层验证及提交；相邻条款不自动关闭。

| 条款 | 责任记录 | 成功条件与验证出口 | 状态 |
| --- | --- | --- | --- |
| R06-A | B01 | 当前任务认证 CLI 的实际加载代码/源码身份可查，旧尝试不能冒充新执行 | 已定向验收；限认证 CLI |
| R06-B | B01/B06 | 源码、控制器、worker、验证目标分列，多页面一致；旧执行并存可准确显示 | 待关闭 |
| R06-C | B06 | 更新失败保留原运行及恢复入口，适用环境中验证接管/回退 | 待关闭 |
| R02-A | B04 | 接续发送结果未知有消费者核对，已送达不盲重发，无法证明则保留明确等待 | 消费者与实际 hook CLI 丢通知/重建/暂停回归通过；真实 Provider 全旅程待验 |
| R02-B | B04 | 控制器/诊断退出、漏通知后唯一责任者接续实际工作；暂停保持 | 待关闭 |
| R02-C | B04/B01 | 在途工具与有效推进分别观察；恢复后核查原任务；损坏账本不造历史 | 续接与清理遇损坏账本拒绝副作用；其他恢复路径待关闭 |
| R04-A | B05 | 受控执行和孤儿工具持有资源时，安装/清理/新 writer 均不能破坏它 | 受控进程及可观察孤儿已验；未知外部持有者资格保留 |
| R04-B | B05/B06 | 清理请求、执行和回执可跨重启恢复；未交付/占用/未知资源保留 | 已定向验收：本机 Git、显式 CLI 重放；非自动后台清理 |
| R04-C | B05 | Node/Python/Go 真实兼容复用、失效准备、失败保留及替换回退 | Python/Node 离线实测通过；Go 按用户要求暂缓，完整替换回退待验 |
| R07-A | B03/B05 | 同 Provider session 丢失后隔离旧执行树，保留脏成果并由新执行产出下一成果 | 正常 sandbox 已修复；真实 Terra High 两个 fresh ephemeral 会话保留脏成果并产出下一功能通过；完整 Task Pipeline 故障接管仍待验 |
| R07-B | B03 | 交接保存/交付/接收各边界中断可恢复，缺页旧身份拒绝，全入口保持 | 待关闭 |
| R07-C | B03 | 适用跨 Provider 组合真实接力、目标权限核对及结果准入 | 待关闭，较低优先级 |
| R01-A | B02 | 大输入保留别名出处、共享约束和责任；删除必需条款的语义负例失败 | 待关闭 |
| R01-B | B02/B03 | 活跃输入修订保留旧版/成果，隔离后绑定新版；AC 改变不能借旧批准 | 旧新快照与同 Package 合同变更的 rebind 提示通过 CLI 回归；活跃切换/批准全旅程待验 |
| R01-C | B02/B04 | 恢复缺页仅补缺页；重复通知不重置；真实恢复/压缩后产生业务成果 | 待关闭 |
| R03-A | B06 | 固定源码补 WebM、上传重试、归档交付不重入 Impl；失回执先核对 | Verify/Integration 局部重试回归通过；视频/上传/归档完整链待验 |
| R03-B | B06 | 同版自检/独立复验/最终验收分列，改名和迟到报告负例拒绝 | 待关闭 |
| R10-A | B03/B04 | 接手者实际消费失败路径/SOP/决定，来源、版本与失效条件可查 | 前任记录认证 CLI 与方法已接线；真实模型采用、全局历史检索待验 |
| R10-B | B03/B04 | 子成果落盘、父接收、最终验收分列；迟到答复不推进错误 attempt | 待关闭 |
| R05-A | B07 | 主预览轻数据与正文/附件解耦；目录/章节不反复加载无关完整包 | 主预览已接轻量 envelope；固定 accepted_plan 章节不重建正文，旧版仅解析选定 canonical 来源；定向 API/浏览器验收通过，不宣称旧版零全文 IO |
| R05-B | B07 | Kanban/Channel 同版入口、新约束采用和统一材料目录完整性 | Plan 绑定材料目录已接实际 API/UI，分页显示类型/大小/版本/来源/可用性，未读与未知替代关系明确保留；全入口新增约束/跨运行材料仍待验 |
| R05-C | B07 | 慢详情、跨项目、SSE 重连、清空/删除/无权/过期/未知保持一致 | 慢正文隔离、原生 SSE 断线重连后批准/删除对账通过；跨项目、来源异常已有回归，全入口组合仍待验 |
| R08-A | B04/B03 | 暂停/取消/审批/维护与重启、部署、凭据轮换、执行者切换组合验收 | 待关闭，已有暂停宿主资格 |
| R09-A | B05/B02 | Datal 原要求到 DAG 独立评审，尾部集成有明确边界和证据生产者 | 只读结构与尾部风险评审完成；45 任务无环，P20/P20Q 有写域和生产者但责任过宽；实际重切分/签审未关闭 |
| R09-B | B05 | 真实 8 Impl/8 Verify 分别产出及窄前沿对照，不用 mock 代替 | 待关闭 |
| R11-A | B01–B07 | 所有条款生产/消费、同版证据、实现/合入/采用逐条对账 | 进行中 |
| R11-B | B06 | Datal 八类产品资格按 v1.4 分列；正式数据/硬件不足保留条件 | 八类差额已分列并复核 51 来源/10 绑定摘要；149+44 映射不代表产品通过，正式数据/硬件资格仍受限 |

执行顺序：验收索引 → R06/R02/R04-A/R07-A → R01/R03/R10 → R05/R08/R09 → 条件性 R07-C/R11-B。
R04-B/C 随资源主链推进。原 Datal 暂停保持；真实 Codex 使用 GPT-6.1 Terra。
不以回执字段存在、进程启动或测试数量替代相应成功条件。


## 2026-11-15：实际采用、未知发送与进程占用修正

本轮沿用原 B01–B07，不另建任务状态机。补齐 `worker_load_receipt` 的真实入口：
认证成功的 `artifact index/read/recover` CLI 记录当前 operation/attempt/dispatch、
manifest 摘要、源码 revision、实际 Python code object 摘要及必需 sidecar。
版本投影同时核对当前 TaskStore 与 operation；旧派发、损坏凭据和 worker 自报不计入采用。
Dashboard 展示 `observed/active CLI observed`，不把该凭据解释为模型遵循、provider 全量升级
或干净 checkout。metric 缓存保留事件 origin，避免重建后丢失信任边界。
凭据写入复用 artifact context 的筛选视图；回归测试禁止退化为完整 telemetry 回放。

接续发送前持久记录唯一 claim、不可覆写的提示路径和摘要；stream turn 携带 continuation id，
native Codex UserPromptSubmit 只保留输入摘要。idle replay 消费匹配当前 attempt 的收件事实，
追加 `worker.continuation.reconciled`，Run Manager 和交接未知效果清单消费该事实。
无匹配证据仍为 unknown，不猜测未送达，不自动重发外部效果，也不标业务完成。
完整 provider hook 故障注入尚未验收；本轮两会话真实实验不替代该资格。

环境执行保护增加 PID/start-time 持有凭据及受控子进程继承的资源声明。
安装、资源重声明和清理在独占锁内检查实际进程；进程退出后回收过期持有凭据。
同 UID 可观察的 cwd/fd/mapping 占用也阻止修改。新 writer 启动前检查当前及前任工作区，
即使 transport 已报告父会话退出也保留残留子进程占用和未提交成果。
共享依赖的其他读者不因 writer 工作区检查被误当成旧 writer。
这不是对抗恶意隐藏资源的 OS 沙箱：其他 UID、不可观察且未登记的外部资源、容器/远端
生命周期仍未完整覆盖；不将这些未资格化情况宣布安全可清理。

真实验证以 GPT-6.1 Terra High 在隔离目录运行两个全新 ephemeral 会话：先实现 subtotal，
再从交接材料保留未提交函数并实现 total_with_fee；独立 Python 断言验证结果与错误输入。
首次宿主嵌套 sandbox 启动失败保留证据；隔离实验使用显式无嵌套 sandbox 入口后通过。
该实验不是 Datal、完整 Task Pipeline 故障恢复、跨 provider 或 8+8 吞吐证明。
最终性能修正发生在真实实验后，仅改变观察事件的父事件查询；已补读取/CLI 回归，未冒称
真实实验覆盖此后每个字节的同版最终产品资格。

验证和首次失败记录见 [本轮修正报告](../../reports/continuity-closure-20261115-review.md)。
原 Spec 仅修正已获授权的 confirmed 状态，正文未改；当前 SHA256 为
`71e7906125a91123cb2eeef1ec50e5951595d975b0c7f79218503037eb564973`。
R01/R03/R10 与 R05/R08/R09、R04-B/C、R06-B/C、R07-B/C、R11 仍按清单推进；
所有 B01–B07 保持 active，不因本轮局部通过归档整项。

## 2026-11-15：受限验证入口、持久清理与前任材料消费

真实 Codex 测试入口已删除绕过 sandbox/approval 和忽略用户配置的参数，固定为
GPT-6.1 Terra High、`workspace-write`，附加可写范围仅为该实验 state 目录。
正常 sandbox 预检返回 `bwrap: loopback: Failed RTM_NEWADDR: Operation not permitted`。
本轮没有通过提权、换账号或关闭 sandbox 继续，也没有新的真实模型验收结果。
上一节无嵌套 sandbox 的历史实验保留原记录，但不计入正常安全边界下的资格。

`zf stop --clean-workdirs` 先记录当前 state dir 内的确切 Git worktree/分支身份，再尝试
安全清理；`zf stop --resume-cleanup` 仅重放原请求，不纳入后来新增的工作区。两参数互斥。
两条命令都会执行原有 stop 逻辑，不应用于仍需运行的项目；本轮仅在隔离测试中调用清理。
`workspace.cleanup.requested/worktree_removed/completed/blocked` 通过 EventWriter 保存事实。
中断后核对 Git 和原物理身份，解决删除成功但回执丢失；不强删、不重建原账本。
控制器无停止回执、未交付 Task、活跃 attempt/operation、占用、脏成果、路径/分支复用或
损坏账本均保留资源。`blocked` 注册到问题目录供诊断，未授权后台自动删除或外发通知。

续接链新增损坏账本检查。真实 `hook-recv` CLI 子进程保存收件事实后模拟通知丢失，
重建 Orchestrator 后只核对一次，暂停仍保持；该层证明 hook/consumer 接线，不能冒充
Provider 自行发出 hook、完整宿主进程崩溃恢复或最终业务成果资格。

TaskContractAuthorityService 的不可变变更回执补存旧合同与旧执行绑定，事件明确由 kernel
产生。后继 Task Pipeline 交接携带当前权威版本的 mutation ref，核验摘要和 Task 身份，
旧格式缺快照明确呈现不可用。原合同和新合同可追溯，不将历史批准移植到新范围。

`zf artifact work-record show --operation <当前 operation> --predecessor [--evidence <n>]`
沿用当前凭据/Task/lease/input 认证，仅读取本次固定交接的直接前任记录和证据；分页仍绑定
摘要。错误 target、过期 dispatch、缺失/损坏原正文拒绝读取，不用交接内嵌副本掩盖损坏。
现有 `evidence_files` 可保留带来源、适用版本、失效条件和替代说明的 SOP/失败路径；
语义方法在 `zf-harness-done-contract`，不增加第二套任务状态或 runtime 语义判断。
读取不等于 handoff-accept，更不等于业务验收。全局历史搜索与真实模型采用仍未关闭。

## 2026-11-15：沙箱预检、预览分层及差额复核

真实 Codex 入口在付费模型回合前先运行当前 CLI 的
`codex sandbox --permission-profile :workspace -C <isolated-root> -- /usr/bin/true`。
失败留下命令、退出码和 stderr，不降级到无沙箱。宿主 Codex 0.164.0 使用 bundled bwrap；
系统未安装发行版 bubblewrap，Ubuntu 24.04 的 unprivileged user namespace 限制开启，
实际失败为 RTM_NEWADDR Operation not permitted。与 AppArmor capability 限制一致，
但未读到 kernel audit 原始拒绝日志，不能将推断写成已证实的具体规则命中。
管理员修复步骤见 [宿主修复](../../reports/continuity-sandbox-host-remediation-20261115.md)，
保留 AppArmor 与 sandbox；本会话不修改 /etc 或降低权限边界。Go 继续按用户要求暂缓。

Inbox 主预览改为 `/plans/{plan_id}/preview/summary` 的轻量 envelope：保留审批身份、
动作和绑定目录，正文明确 deferred。完整原文、工程上下文、待决登记或编辑操作才取全审阅体；
来源与任务输入不读取无关结构化包。摘要/版本路由禁缓存，完整体迟到或审批身份变化不替换当前视图。
固定 accepted_plan 目录/章节校验版本并复用索引；旧格式仍可能解析选定 canonical Plan，
只保证不重建无关 dossier。无摘要的旧来源通过 `/artifacts/{name}/version` 固定本次读取快照，
界面明确这不是审批时版本。未访问的正文不以空字符串冒充完整交付。

原生 EventSource 断开并重连后，Inbox 主动重新读取当前已打开 Plan；15 秒刷新也是补偿入口。
审批完成更新按钮，Plan 删除关闭旧预览并提示；迟到刷新不能重开已关闭或覆盖新 Plan。
浏览器故障注入对原生 SSE 流作有限响应并让客户端重连，状态变化以路由注入；
这证明 UI 对账，不冒充真实业务审批全过程。浏览器夹具使用 Uvicorn lifespan 在 SIGTERM 后
追加 simulation.done 并清理自己的隔离目录，外置原子回执保留清理事实。

本次重新绑定 Datal 51 个原始文件、10 个 Plan 引用；149 组要求有映射，但八类产品差额仍在。
P20/P20Q 的写域和证据生产者存在，尾部责任仍过宽，见 [Datal 对账](../../reports/continuity-datal-reconciliation-20261115.md)。
用户要求的最小 TypeScript PRD 全真实验收已准备隔离方案，须在剩余能力收口及正常沙箱资格后启动，
不提前以小演示代替 SPEC-002、真实 8+8 或 Datal 外部资格。

本轮完整证据和未完成清单见 [修复与验收报告](../../reports/continuity-sandbox-preview-20261115-review.md)。
R05-B 统一材料目录/全入口约束、恢复全旅程、环境替换回退、跨 Provider、真实并行等仍按矩阵保留，
不是全部由 sandbox 一个故障造成；B01–B07 均保持 active。

## 2026-11-16：宿主修复落地与材料目录

用户明确授权本机安装修复。安装发行版 bubblewrap 0.9.0、apparmor-profiles、apparmor-utils，
加载发行版 `/etc/apparmor.d/bwrap-userns-restrict` 后，Codex 0.164.0 的正常受限命令通过。
仅安装包仍复现 RTM_NEWADDR，加载 profile 后通过；全局 userns 限制仍为1。
普通用户的实际负例确认工作区内可写、外部自建测试文件只读且原内容不变；未给 Codex root。

跨节点入口为 `bash scripts/setup-codex-sandbox.sh --check|--apply`，自动修复限本轮验证的
Ubuntu24.04。只缺包时安装，已有相同 profile 可重复加载，不覆盖定制或符号链接配置，
不关闭全局安全限制；`--apply` 最后仍以原用户探测。其他系统只给检测/发行版安装说明。
`zf doctor provider` 现在调用实际 Codex workspace profile，不再用需要 host capability 的
`unshare -n` 误判；未知 CLI、超时和启动失败明确非零，不建议 bypass。探测不消费模型额度。
真实验收入口与 doctor 共用 `runtime/provider_sandbox.py`，避免测试脚本与部署检测口径漂移。

GPT-6.1 Terra High 在正常 workspace-write 下完成两个全新 ephemeral 会话，认证 CLI 读取
四次均有采用观察，第二会话保留 subtotal 并实现 total_with_fee，独立正/负断言通过。
这是同 Provider、普通沙箱下的实际接力子资格；不是完整控制器崩溃恢复、跨 Provider、8+8
或最小 PRD 全流程。源会话自然退出，不能称作进程中途崩溃试验。Go 仍按用户要求不测。

Plan 新增 `preview/materials` 与“材料目录”阅读视图，每页最多100条（UI50），只 stat 当前页
绑定来源，不读取正文。显示文件类型、规模、固定摘要/未固定版本、来源记录及未知替代关系。
未读正文不显示校验成功；空文件、不可用、无权、未知分列，缺失文件不能凭猜测标“已删除”。
审批请求身份变化返回409；失效包整体拒绝；选定 Markdown 再按章读取，其他格式不冒充可读。
目录请求的迟到响应由 project/Plan/请求/分页身份隔离。Kanban/Channel 全入口新增约束旅程、
跨运行历史材料和真实替代关系生产者仍未关闭。

实现与宿主包/profile身份、真实 Provider、浏览器证据见
[本批报告](../../reports/continuity-host-20261116-review.md)。保持 B01–B07 active，尚未 merge/push。

Python/Node 使用隔离的离线依赖实测共享复用、声明身份变化、占用时拒绝重建、准备失败
不伪就绪及候选保留。Go 不在当前 PATH，保留 skipped；未安装宿主工具或下载依赖。
这不是完整包管理器/ABI 支持矩阵，也不证明自动蓝绿替换与回退。

本轮验证、首次失败和命令见 [续作报告](../../reports/continuity-next-20261115-review.md)。
所有 B01–B07 仍为 active；未合入 dev、未部署、未恢复暂停的 Datal。

## 2026-11-15：继续验证，Go 暂缓

用户要求暂时不测 Go，保留原规格条款，仅暂缓本轮 Go 验收；恢复条件是用户重新要求。
其他验证发现并修复以下缺口，继续沿用 B02/B06/B07：

- 同一 Plan Package 内只修订 Task 合同，原 `input_lifecycle` 会直接返回 current。
  对明确属于 Task 域且提供 state dir 的读取，现在核对当前 Task/operation/dispatch/合同
  绑定；不同则返回 stale + requires_rebind。实际 recover/index CLI 能指出受控重新绑定，
  不自动改 dispatch、批准或历史输入。其他任务继续沿用其有效旧 generation 的行为保持。
- 普通审批返回后的刷新缺少请求身份检查，能够覆盖新 Plan 或重开已关闭预览；现在普通
  与 Owner 审批共用绑定当前页面、项目和请求序号的刷新，迟到结果不更新新选择。
- Owner 决策后原来只刷新 Inbox，预览仍为 pending；现在同时读取最新预览。普通审批的
  busy key 与 Inbox item id 不一致导致按钮未禁用；现在同时识别 Plan id 与队列 id。
  动作仍由既有 token-gated API 和 request/proposal/eval 身份控制，未放宽后端审批。
- 下载一个绑定附件、读取一个任务的 input-review，原先仍构建全量 Plan review。
  来源目录解析集中到 `bound_plan_sources/bound_plan_source`，章节、下载与输入审查复用
  相同绑定，仅读取所需正文；保留路径、摘要、歧义与版本错误。主 preview 和旧版
  accepted_plan 恢复仍可能整包读取，本批未将 R05-A 宣布整体完成。

定向后端闭包 444 项通过，覆盖输入、审批、局部 Verify/Integration 重试、实际 Web 路由、
附件/章节和历史兼容。前端 build/typecheck/unit、sentinel 53 和 Flow mock smoke 10 通过。
Docker Chromium 最终 28 项通过，保留实际页面业务断言及 trace/WebM/截图；审批写请求只由测试路由模拟，
读取使用隔离真实 API，不触碰正式项目。章节测试加入 plan-review 的默认匹配清单。

完整失败、修复、最终浏览器数量和源码身份见
[继续验证报告](../../reports/continuity-validation-20261115-review.md)。
本轮正常 Codex sandbox 预检仍因 bwrap RTM_NEWADDR 权限失败，未运行真实模型。
浏览器视频是本次阅读与审批 UI 的证据，不冒充 R03 的 Datal 产品视频/上传/归档全链验收。

## 2026-11-16：真实 PRD 验收的 worker sandbox 入口

显式 `roles[].provider_session.sandbox: workspace-write` 让 Codex 首次启动与 resume
使用 `--sandbox workspace-write --ask-for-approval never`，不回退全权限；该字段进入
会话不可变配置摘要，变更权限使旧绑定失效。只接受 Codex，此字段不改变逻辑角色权限，
未配置的历史项目保留原行为。部署宿主仍需通过 `scripts/setup-codex-sandbox.sh --check`。
仓库生成的 telemetry hooks 仍沿用现有受信来源策略，OS sandbox 开关不替代 hooks 审核。

回归覆盖 loader、首次与恢复 argv、配置身份变更、错误值与其他 Provider 拒绝、tmux 调用方。
真实 PRD 实验使用独立 `/tmp/zf-minimal-prd-*` 项目，结果另记，不因开关实现宣称端到端通过。

## 2026-11-16：v5 真实 PRD 集成验收续作

按用户批准顺序先修复配置/Plan，再验证正常交付和故障接续。
`tests/continuity_prd_fixture.py` 从当前 `zf-prd-v5.yaml` 派生隔离验收配置，
保留 WRC/blocking、固定候选、Critic、人工 Plan 批准；两条 Impl/Verify lane，
Terra High 与 workspace-write，各阶段和 Run 不设 token/执行时长上限，其他门禁保留。
Reader 使用 dispatch 范围，工作区保持；基线实验关闭额外 resident 修复 Agent，
不得将基线声明为已完成无人监工恢复。后续故障实验独立记录。

实际派生配置复现 lane_role_template 不接受 sandbox；补齐共享 ProviderSessionConfig
解析，保留非法值拒绝。Planner/Reviewer 方法补充浏览器命令、AC、owner/tier、
实际阶段、证据路径和候选身份一致性；已知 locator 优先，避免反复索引遍历。
语义判断仍在技能，运行时不按产品类型硬编码验收。

验收记录见 `reports/continuity-prd-v5-20261116/`；沿用 B01–B07 active，
真实流程或故障场景未通过前不得归档或声称完整规格通过。

本批启动前定向验证：配置/lane生成/Reader轮换/技能分发179 passed；sentinel53 passed；Flow mock10 passed。沙箱命令和Docker Chromium启动通过，尚不等于真实PRD通过。自动planner因共享配置选择全量，采用显式闭包替代；一次误用skip-tier名称触发的全量进程已定向停止，未计为通过。

真实 v5 启动再现占用登记顺序缺口：tmux 先创建项目 cwd 的 shell，之后首次登记源依赖被正确的占用检查拒绝。`zf start` 现于 transport 初始化前登记源资源，不豁免持有者或清理保护。启动入口 RED 后78项启动/资源/环境闭包GREEN，sentinel53、Flow mock10通过；同一已接纳请求继续恢复，保留首次启动失败。

真实v5 Planner首轮失败并非token/timeout：组合shell补丁触发保守写域拒绝，随后把$(sed...)作为字面补丁导致无效hunk。单独原生scratch更新已成功。保留guard，补planner方法：具体补丁、独立literal heredoc、产物与scratch分写；长文分段防截断。通过受控stage-replan-new-generation绑定原失败恢复，不冒充产品质量通过。

## 2026-11-16：任务能力预检与真实阻塞

真实 v5 经独立 Critic、Docker Preview 和授权 Plan 批准，已派发 SCAFFOLD。
同一脚手架测试宿主运行2/2通过，但 Worker 内监听 EPERM；Git worktree 索引只读，
Worker 诚实提交 dev.blocked，没有伪造自检或提交。Docker socket 访问也被沙箱拒绝。
这证明启动探测通过不代表任务执行能力就绪。

新增显式 CLI 入口 `zf doctor provider --workdir <worker> --capability local-listen
--capability docker --capability git-metadata --json`，与启动结果分开记录；
不改权限、不写 Git、不启动模型。真实失败现场三项均正确报告 blocked；
命令接线与失败/超时/非法项回归17项通过。用法与资格限制见
[预检手册](../manual/16-real-codex-provider-preflight.md#11-对实际任务工作区检查能力)。
这只是诊断改进，尚未新增沙箱外受控测试/提交入口，也没有通过完整 PRD 或故障旅程。

## 2026-11-16：受控宿主协助与断点恢复

用户授权实现受控宿主路径，同时本 session 逐项审阅。新增 `recover host-assist` 仅
预览：必须在项目上下文运行，绑定已接纳且已结束的阻塞 Impl/Task Verify operation、
Run/Task/attempt/lease/dispatch、冻结合同、派发工作区、HEAD、源码文件摘要及声明依赖
内容摘要。当前不支持活跃 Worker、暂停/终态 Run、跨仓目录、删除文件或带活动 Git hook
的提交；这些情况明确拒绝，不自动扩大权限。

`POST /api/actions/task-host-assist` 是唯一新增执行入口，必须提供显式操作员 action token、
approval_ref、reviewed_digest 和原始 manifest；trusted-session 不能替代该动作的 token。
Provider 不持有该 token，Kanban Agent 自动动作清单没有加入本动作。
执行前在既有资源排他锁下重读身份/源文件/依赖，检查真实持有者；拒绝已有暂存内容，
提交路径必须精确等于任务全部变更且位于已分配写域。无强制暂存、跳钩子、amend/merge/push。

验证只运行当前阶段合同中的原始命令，要求干净的固定目标；宿主子进程只接收 PATH、
临时 HOME/TMPDIR、LANG，不继承 action/provider 凭据。保留真实退出码和完整日志，
前后源码/依赖改变即失败。宿主代码执行须由操作员审阅：该入口不是任意代码的安全沙箱。
命令采用合同的1–900秒有限探测时长；它不修改v5阶段和Run的token/时长配置。

事实通过既有 runtime.action.attempt.started/completed/failed 与原子 sidecar 保存。
相同manifest重复执行返回原回执；已有started但无完成时保留未知效果，拒绝自动重跑。
当前该未知状态需人工核查，尚不宣称自动效果核对/丢通知恢复全部完成。
动作不产生task.done/dev.build.done/test.passed，不修改原失败结果；后续使用已有
recover task-impl/task-verify与当前身份进行结果准入。必需新增的Verify宿主探测必须绑定自己的operation；原Impl回执不能冒充新独立执行。
冻结合同明确允许的同版确定性命令仍可走既有reused_command_receipt_ids复用，
不得为本入口抹去合同复用许可；独立语义审查与Judge仍保留。

真实 caller 是 CLI preview→Web token gate→ControlledActionService extension；
实现：task_host_manifest.py、task_host_actions.py，测试 test_task_host_actions.py。
本增量只覆盖Task Pipeline已结束的阻塞阶段；全局Candidate Reader和无人监工授权策略
尚未据此获得资格。执行记录继续保存在本轮 reports/continuity-prd-v5-20261116-review.md。

续审修正：Task Verify 的环境失败是 `execution_status=failed, verdict=abstained`，
不是 Impl 的 blocked。宿主入口仅在同身份已接纳 verification-result.v1 证明这个组合时
允许独立 Verify 的探测；completed/abstained 不获得执行资格。RED复现后120项
宿主/Impl重试/Verify重试/typed admission闭包通过；不改变产品 verdict。

真实恢复后再现跨阶段交接缺口：Impl结果通过且回合结束，驻留进程仍占Task Workspace，
独立Verify正确地被exclusive occupancy拒绝。新增 task_workspace_handoff 在实际
Task-stage activation入口释放同Run/Task/map/binding、已准入settled且回合已结束的前任；
任一活跃operation/attempt、回合后新活动、其他任务或身份不明均不获释放资格。
沿用原生命周期服务保留session/workdir；随后原样检查真实占用，残留子进程仍阻塞。
不靠降低idle阈值或豁免占用解决。6类正反例和调用方/生命周期/资源闭包81项通过，
sentinel53通过；首次mock与真实继续情况见本轮报告，不能据单测宣称无人监工资格。

TODO-JOURNEY真实产出的browser runner复现镜像内`/work`解析Playwright失败；模块位于
`/app/node_modules`，不是宿主缺浏览器。操作员保留原脚本后修复受限任务源码，补齐
存储异常/损坏值保留/键盘旅程、实际image ID、失败trace/video和自有服务进程收尾。
通用经验进入dev-worker技能并同步分发；产品脚本仍归隔离验收项目，不硬编码进Kernel。
本轮预冻结执行只作runner诊断，正式候选冻结后的重新验收仍是必要门禁。

## 2026-11-16：Candidate 环境阻塞与误重规划修复

真实最小 PRD 已完成两个任务的 Impl/Task Verify，并冻结候选。Candidate Verify 的
`verification-result.v1` 返回 blocked/abstained；fanout 子事件保留 schema，但 manifest
白名单及 settled replay 丢失 schema，导致聚合缺失 failure_kind，被误送回全局 Plan。
投影现保留结果类型及失败分类；重放同样保留。聚合发布阶段事件同步透传分类，
避免仅中间投影修复而恢复消费者继续误判。语义 rejected 不被转换为 infra。

受控 `stage-replan-new-generation` 的 fixed-input repair 可从历史子操作已接纳结果的
ref/digest 重新核验基础设施失败；不修改旧事件。显式 `misrouted_operation_ids` 仅允许
同 Run、因果链下、已停止且未接纳结果的 Plan reader。存在进程持有者、新计划发布或
未列出的活跃后继即拒绝。取消通过 WorkflowOperationService，fanout 由 EventWriter
记账；随后恢复原 Reader 输入与候选，保留人工审批及既有任务成果。

`task-host-assist` 同时接受已接纳 unavailable Candidate Verify 的只读合同探测。
固定 Run/map/operation/request、不可变合同/target/freeze receipt、当前候选及实际 Reader
worktree；要求同仓库 detached exact HEAD、源码干净、资源空闲。仅执行当前 candidate_verify
合同命令，禁止 Candidate commit。结果仍是宿主执行收据，不自动产生产品 verdict；独立
Reader 必须审阅并以当前身份提交。宿主执行不等于 Provider sandbox 获得 Docker/监听权限。

验证覆盖真实 Git detached currentness/负对照、实时与 settled replay 分类、实际聚合调用方、
历史 sidecar 篡改/缺失/跨 Run、误派发取消及活跃持有者拒绝。完整交付、Judge 和无人值守
故障旅程继续单独验收，不因本补丁完成自动标为通过。

fixed-input repair 同样接入已有 operator_feedback→rework_feedback 绑定，保留操作员
来源和请求事件。修复事实/证据位置进入后继Reader上下文；这不是预先批准其产品判决。

分类为 attempt 局部结果：新派发及结果投影重置前次 schema/verdict/failure kind/class，
防止一次 infra 失败污染后续真正的 rejected。重试质量拒绝负对照已覆盖。

真实派发复核纠正上述接线边界：仅写rework_feedback事件仍不足，source-manifest模式不会
把inline trigger正文直接交给Reader。CanonicalHandoffResolver现将修复说明和原始来源
落为不可变reader-repair-context sidecar，以critical-reader-repair-context登记为完整必读
输入，并纳入source snapshot摘要。大正文仍按既有分页读取；当前已派发attempt不修改。
下一代须重新读取，既有读收据不因消息相似而继承。已用真实resolver→manifest→required
read→正文读取链路验证，而非仅断言恢复事件携带字段。

真实 Discovery 暴露 workflow-read 结果二次规范化缺陷：第一次把 blocked/abstained 的
状态统一为 failed，第二次据此覆盖显式 recommendation，误变 semantic_rejection。
现在优先保留明确的 block/abstain/reject/needs_rework；缺省 recommendation 也保持
原 subject verdict。非产物型 Reader 的显式 reader_execution_failure 同样保留。
实时 fanout、settled replay 和历史受控恢复均传递已接纳 failure_class；未声明执行失败的
普通弃权不自动推断为 infra，拒绝结果也不转成通过。覆盖实际 SemanticResultSubmitService
的重复适配、实时 stage 聚合及历史 ref/digest 恢复。原历史事件与已冻结输入保持不变。

复扫方法补充于 zf-verify-rescan-replan/references/product-convergence.md：普通分页未完成
继续补读，不能以abstain替代消费义务；核验当前集成候选全局收据，区分已有合格证据、
当前阶段明确要求的新probe与未覆盖风险。保留独立完整产品审计和PAS，不把环境重跑失败
解释为已有证据失效。方法变更供后续materialization，当前attempt不冒称已热加载该文本。

## 2026-11-16：最小 PRD 正常流程真实闭环

Run `minprd-v5-r2-015713` 在 05:26:54 UTC 产生内核事件
`run.goal.completed`（`evt-66199a56b74a`），交付状态 `ship_delivered`。
已走过真实审批、两任务 Impl/独立 Task Verify、候选集成、两条 Candidate Verify、
产品完整性复扫和 Thin Judge。Discovery 缺少 PAS 字段曾被准入拒绝，原操作内补充
绑定后通过；最终 Judge 也由真实 Terra High 提交，经机械门禁完成交付。

冻结候选 `e4fc724e09ce4794dfdd64301c257b5cee788a42`；隔离产品仓库交付提交
`fe3654ac4fcef603f54b22d6b9402f49df619179`，两者源码树一致。
最终运行时版本 `c1c0757e`；45 个不可变引用摘要核验通过。
内核自动记录 `simulation.done`（`evt-31f3b98cf591`），退出自有 tmux/controller；
操作员关闭隔离 Web 8002，保留源码、账本和证据，未修改真实开发环境 8001。
本地证据备份见 `reports/continuity-prd-v5-20261116/final/audit.json`，包含交付源码归档、
两 lane 浏览器产物及已核验收据；完整过程见 `reports/continuity-prd-v5-20261116-review.md`。

资格边界：本轮包含操作员宿主探测、问题修复及受控恢复，不是无人值守资格；
2-task DAG 不证明 8 Impl/8 Verify 满载，也不证明跨 Provider、Go 或整个 207–209/spec 完成。
待改进仍包括 Discovery 完整配置强制分页、pin/setup 占用未区分 infra、聚合 goal_id
父子来源差异和自动 ship 的非 Conventional merge 标题。这些保留为问题，不能由正常闭环
掩盖；原 B01–B07 仍按各自完整验收条件保持 active。

## 2026-11-16：最小 PRD 复盘 P0–P2 修复

本批对应 `tasks/done/2026-11-16-0559-prd-long-run-p0-p2.md`，沿用真实最小 PRD 发现的问题，不改写其旧事件、attempt 或
交付提交。实现、回归过程和本地证据详见
`reports/prd-long-run-p0-p2-20261116/review.md`。

- **P0 环境等待**：Reader pin 的占用检查移至 prepare/checkout 前。临时占用记录
  `fanout.child.dispatch_deferred` / `reader_environment_busy`，保持 pending，既有
  level-triggered Reader 恢复入口继续尝试同一 fanout，控制器重建后也可恢复；不生成
  产品失败或消耗语义返工次数。错误目标/真正 pin 失败仍拒派发并标记 infra。
  仅 on-demand、无活跃 operation/turn/task/child 的受管 Reader 可以通过既有生命周期
  服务休眠；未知占用者和存活子进程继续阻塞，不绕过真实资源检查。
  Task Workspace 的 provision/setup 占用通过 `TaskWorkspaceBusyError` 接入
  Task Pipeline 的 `waiting_for_environment`，避免误计为派发故障冷却。
- **P0 共享 setup**：新增 `project.setup_scope`，默认 `workspace`，保留每工作区
  可能产生本地文件的脚本行为。只有显式设置 `shared_resources` 才允许跨工作区复用；
  该声明表示脚本的依赖输出均在声明的共享资源中，必须同时提供 setup 脚本、
  `project.setup_inputs` 和 `runtime.workdirs.required_paths`。
  setup_inputs 应包含安装脚本、依赖锁文件以及影响准备结果的工具版本/数据身份文件。
  provision_paths 只负责接入路径，不独自证明准备完成。
  复用收据核对脚本/输入摘要、逻辑声明、物理目标及实例，并绑定资源 mutation epoch。
  原地安装、资源替换、输入变化及失败安装均使旧收据失效；失效时等待独占锁后准备。
  首次准备应在消费者启动前完成；活跃消费者只允许复用合格收据，不能强行安装。
  Reader、Task Workspace、Candidate 三个入口均接入该配置；Candidate 仅在显式共享
  模式下允许复用，默认模式仍强制执行候选 setup。
- **P0 候选合同保真**：聚合保留每条命令的 tier、producer_paths 和每条 AC 的
  verification_tier。相同命令合并产物路径、保守保留非确定性；tier 冲突拒绝聚合。
  Candidate 阶段本身的 owner/tier 仍为 `candidate_verify/integration`，这不覆盖
  其下命令的 `e2e` 或 AC 的规范层级 `real_e2e`；旧快照不重写。
- **P0 Goal 身份**：显式父 trigger/manifest 的 goal_id 优先于 pdd_id；仅父级未绑定
  Goal 时采用旧 PDD 回退，不能把子报告/聚合投影中的别名提升为父级身份。
  冲突仍显式记录；实时聚合和受控 aggregate rebuild 共用规则。
- **P1 输入加载**：存在已批准精确 Flow 时，原始输入及 `critical-approved-flow`
  保持完整必读，`approved-config-full` 留在同 attempt 的可检索源中，允许按需读取。
  无精确 Flow 的旧流程仍要求完整配置；修复说明、原需求和其他阶段必读证据不减少。
  只影响新 materialization，不修改已派发 attempt 或继承旧读收据。
- **P1 Preview**：从同版不可变包读取 PAS，展示用户旅程和断言；旧 Plan 缺独立摘要时
  展示已绑定 Goal，缺业务分组时提供任务目录，并明确标示缺项和“规划不等于通过”。
  PRD synth 方法要求新 Plan 编写产品摘要、旅程、分组，三份技能同步；UI 不编造语义。
- **P2 交付提交**：自动 Ship 的新产品 merge 使用
  `feat: deliver verified product candidate`，保持 --no-ff、目标校验及冲突处理。

验证分层：真实临时 Git/资源进程 + 确定性运行时调用链、契约门禁、mock 流程和 Docker
Playwright。浏览器使用已有镜像 `mcp/playwright:latest`、独立端口 8003，保留业务断言、
trace、截图及 WebM；fixture 正常退出时记录 simulation.done 并清理自有临时状态。
本批修复不新增真实 Provider 故障矩阵结论：8+8 满载、跨 Provider 接力、Go、长时间无人
监工资格及原 B01–B07 的剩余验收仍独立管理。持续由未知占用者持有的环境仍需等待或
受控运维处理，不能通过强杀不明进程来宣称自愈成功。

## 2026-11-16：无人监工故障验收前置修正

真实故障验收启用 resident Run Manager 时，发现其动态生成的 Codex Role 没有
provider_session，仍会落入 adapter 的 legacy sandbox bypass 分支。生成入口现在显式
使用 workspace-write 并禁止 native delegation；逻辑 permission_mode 与受控动作门禁
不变，Claude effort 设置保持。实际 Role→adapter argv 的 RED/80 项调用链回归、
53 项 sentinel 和 10 项 mock 流程通过后用于新的隔离验收。
本机 Codex 0.164 的探测命令为 `codex sandbox -- /usr/bin/true`；真实 Terra High
workspace-write exec 已调用 shell 成功。前置人工修复不计为无人自愈成绩。
后续故障事实/结果保留在 `reports/unattended-faults-20261116T090219Z/`。


## 2026-11-16 执行阶段真实故障修复

任务：`tasks/active/2026-11-16-1053-unattended-reader-recovery.md`。
来源：`reports/unattended-faults-20261116T090219Z/review.md`。

- Reader预注册只准备并绑定目标，实际派发才激活Provider；占用时不反复启动。
- Reader环境等待沿用角色stuck阈值（至少60秒），跨重启、排除显式暂停和stop到resume停机区间累计；
  先检查是否释放，再判断超时。超时成为具名infra失败，按fanout/child/run
  路由至Run Manager，Task已done也不能掩盖工作流义务。资源检查不被绕过。
- Run Manager无进展投影覆盖Reader延期，只有匹配run/fanout/child推进或终止
  才清除；旧诊断不能解决另一阶段。诊断效果接受绑定request的建议，案例仍
  等待后续修复或人工决定，不能据此标业务恢复。
- Node依赖预检按声明和父级node_modules查找；空目录不是依赖存在证明，
  无依赖包不强制本地node_modules。真实命令仍是验收依据。
- Verify明确failed+abstained组合；相同preflight错误两次纠正后按角色方法
  报告阻塞，不能盲猜枚举。validate保持只读，不暗中修改lease或终态。
- 已注册Provider的PID/start身份确认退出且工作区无任何活跃/未知持有者时，
  watchdog可提前恢复；单次pane探测失败仍不能越过新鲜心跳保护。
- 宿主验证继续使用有token、approval_ref、精确manifest digest的受控入口；
  复验前检查已有Docker镜像、容器Chromium、宿主监听和合同命令能力。
  不把镜像存在等同于Provider沙箱可访问Docker，不授予任意宿主shell权限。
  产品命令结果、源码稳定性、目标提交和浏览器证据仍必须分别准入。

真实资格需以本批复验记录为准；单元通过不替代无人闭环资格。

本批回归进一步发现并修复：版本投影读取失败必须走Dashboard旧值保留路径；
已认证的当前operation可按Task当前dispatch精确选择operation-v2 attempt lane，
历史lane不能阻塞新代handoff接收。未绑定operation的恢复查询仍拒绝歧义。
进程观测worker.runtime.observed不计为agent语义重复决策；真实worker重复决策检测保留。

真实复验补充发现：候选Reader采用冻结候选合同，输入恢复却只检查普通Task派发，
误报task_binding_changed。现从活跃operation及不可变request证明manifest身份，
复用结果准入的候选currentness校验；冻结目标、子Task合同、sidecar摘要、取消和
新attempt继续失效关闭。修复不授予执行权限，不允许读取旧代包替代当前包。
定向影响闭包756 passed/7 skipped，sentinel53和mock10通过。

同批真实后续阶段暴露workflow-read复扫也没有Task实施合同。无Task合同的
workflow-read/thin-judge-goal-closure仅在自身活跃Reader operation、不可变manifest
及当前冻结目标/Plan绑定全部相符时保留读取；含Task合同者不走此例外。
不同目标、代次、取消、替换attempt或内容篡改继续拒绝；不改变语义门禁。
