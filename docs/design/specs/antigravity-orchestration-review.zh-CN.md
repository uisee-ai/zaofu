# Antigravity 编排、交接与定时执行调研

Antigravity 的模型选择器在一次会话内分配规划与执行。Boost、Swarm 和 Teamwork 才把工作分给不同 Agent。Teamwork 用里程碑和工件交接长任务；会话内定时唤醒、跨会话周期作业、后台进程重启又各有不同生命周期。公开文档说明了这些行为，尚不足以证明业务副作用可以在崩溃后安全重放。ZaoFu 可以采用这些协作方法，继续由 Kernel/WRC、事件、Store 和受控动作掌握执行权。[模型][M] · [Agent 拓扑][U] · [Teamwork][T] · [定时唤醒][S] · [持久自动化][A]

## 1. 执行摘要

1. **[DOC]** Planner 能在执行途中收回控制权；问答可以在规划阶段结束。可借鉴的是“遇到什么情况交回谁”，无需为模型切换创建新业务任务。[模型][M]
2. **[DOC]** Swarm 把所有权、禁改范围、完成标准写进公开 brief；等待成员被点名后醒来。成员静默和 Worker 配额耗尽有不同处理。[Swarm][W]
3. **[DOC]** Teamwork 在执行前约定每条需求如何独立验证，用新的 Project Orchestrator 承接下一里程碑。**[UNKNOWN]** 进程在里程碑中途退出时，文档未给出可重放的提交协议。[Teamwork][T]
4. **[DOC]** Timer 遇到匹配消息会提前取消，由那条消息唤醒 Agent。Daemon Cron 可跨当前任务，仍属于原会话。持久 Automation 则每次开新会话。[Schedule][S] · [Automation][A]
5. **[DOC]** Automation 触发有时间浮动。Sidecar 进程自动重启；由它启动的业务任务是否安全完成需另行取证。[Features][F] · [Sidecars][D]
6. **[INFER]** ZaoFu 最值得先交付的是可重放的一次交接，以及按合同核对的 readiness。公共板和定时器可作为现有受控路径的输入端，后置于执行权与回执验证。[ZaoFu 配套规格](213-zaofu-antigravity-borrowings.md)

下文 [DOC] 表示官方描述的产品行为，[INFER] 表示根据该行为推断或提出设计，[UNKNOWN] 表示截至抓取日的所查材料不足。没有做 Antigravity 客户端或服务端故障注入。

## 2. Orchestrator 术语澄清

| 名称 | 编排单元与交接 |
|---|---|
| 模型选择器 Orchestrator | [DOC] 同一 Agent 对话内两个模型轮流控制。Planner 规划和审视，Executor 操作工具。[Models][M] |
| Boost | [DOC] 主编排者选择实现或调查路线，pipeline coordinator 派首轮与改进 Worker，多轮挑战、修复、复核。[Boost][B] |
| Swarm Coordinator | [DOC] 当前对话充当公开板协调者，横向拆出独立轨道、派具名 Worker、组织 Verifier，并向用户汇总。[Swarm][W] |
| Teamwork Project Orchestrator | [DOC] 由批准后的项目启动流程派出，管理里程碑、并行依赖与专职角色。Sentinel 保留用户请求、路由、汇报和最终审核责任。二者是项目内外两层协调。[Teamwork][T] |
| 普通 custom subagent | [DOC] 父 Agent 下发角色和初始任务，新上下文后台工作，结果返回；权限与工作区依宿主配置。可独立使用，也可作为上述拓扑的基础。[Subagents][U] |

## 3. Planner / Executor 如何交回控制权

**[DOC]** 每条用户消息从只读规划开始。Planner 检查代码、消除歧义、写出可执行步骤与重规划条件。纯解释由它直接回答；步骤已定的小跟进可给短计划。Executor 查找、编辑、构建、测试，并对照计划验证。验证步骤两次失败、接口实际行为不符、计划遗漏设计决策，是回交示例。Planner 阅读当前会话、修改计划，再交回 Executor。长任务及执行中收到新用户消息时，Executor 也会汇报，由 Planner 决定收尾、续做或修订。App、CLI、Enterprise 的可选模型组合不同。[Models，Orchestrator 小节][M]

**[INFER]** 这里的连续性来自同一任务与会话；可以实现局部成本控制，但无法据宣传推导 ZaoFu 的 tokens 或墙钟收益。**[UNKNOWN]** 模型模式本身没有公开的独立 Worker 并行、持久 lease、崩溃接管协议或独立验收者。Executor 自测、Planner 审阅不能为 ZaoFu 的独立 Verify 签收。[Models][M]

## 4. Swarm 的公共协作与生命周期

**[DOC]** Coordinator 按文件、包或调查问题划分互不碰撞的任务，可建立具名小组。每个 Worker 在独立会话从干净上下文开始，公开 brief 写明负责什么、不能触碰什么、何谓完成。Coordinator 负责委派和判断，技术工作交给 Worker。通常每 2–4 人配一个 Verifier，读 diff、跑测试、把问题发回原作者修复；这是惯例，文档未说是硬性配额。[Swarm][W]

| 状态或事件 | 公开行为与边界 |
|---|---|
| Active | [DOC] 当前回合正在思考、用工具或等模型，无“已完成某业务步骤”的含义。[Swarm][W] |
| Waiting | [DOC] 在板上休眠期间不花 tokens。提及个人、组或全体可唤醒相应成员。板显示作者、对象、序号、时间，可回看分工、问答、改动摘要和测试结果。[Swarm][W] |
| 团队停滞 | [DOC] Monitor 平时休眠；未完成且全员沉默时才发帖点人。[INFER] 这能提示诊断，无法定位单个 Worker 丢租约，也不能给替代 Worker 授权。[Swarm][W] |
| Killed | [DOC] 成员永久移除，须新增替代者；独立工作副本会被删除，先 land 修改。[INFER] 替代者可由新 brief 和公共记录重建任务背景。[UNKNOWN] 自动继承被移除者的私有上下文、草稿或精确恢复点。[Swarm][W] |
| 配额耗尽 | [DOC] 显示一次错误后暂停；新板帖、切换模型或用户消息可触发后续活动。额度刷新本身不会自动重启。等待中的 Worker 换模型能保留原对话；底层缓存如何迁移未公开。[Swarm][W] |

**[DOC]** 默认共用 workspace，并行编辑或构建时可给各 Worker 独立 working copy。Worker 之间的协作走公共板，用户仍可直达单个 Worker，消息不登板。普通 subagent 另支持按会话 ID 联系 peer。**[INFER]** 板的显示顺序有助于还原协作经过，无法单独证明所有用户指令、工具副作用均已持久登记。**[UNKNOWN]** 公开材料未给出服务器端文件锁、精确 commit 集成、冲突重算、消息幂等或崩溃后补投规则。[Swarm][W] · [Subagents][U]

## 5. Teamwork 的两阶段流程与角色

**[DOC]** 主 Agent 先访谈目的、受众、需求、约束、目录、验收标准，逐条商定独立验证手段，如测试、基准或按明确 rubric 独立判定。形成可修改的 Prompt Artifact，用户确认后 Sentinel 将项目交给 Project Orchestrator 自主推进。团队会按任务调整组成；轻量单项修改、文档评审与大型分布式开发的角色深度可不同。[Teamwork][T]

| 角色 | 决策、工作与可检查产物 [DOC] |
|---|---|
| Sentinel | 记录请求、路由任务、周期汇报，收尾前派最终审核。[Teamwork][T] |
| Project Orchestrator | 把获批 brief 划为里程碑，管理依赖与并行轨道，委派所有工作，在里程碑间交棒。[Teamwork][T] |
| Explorers / Workers | 前者只读跟踪入口与调用链，评估选项；后者在分配的范围内实现、重构和写单测。[Teamwork][T] |
| Critic / Challenger | 前者独立审正确性、完整性、健壮性和接口；后者用边界、失败路径和最坏资源输入挑战实现。[Teamwork][T] |
| Auditor / Success Auditor | 前者按 integrity mode 核对真实命令输出，识别伪造、mock 或跳过的测试；后者由 Sentinel 派出，做最终端到端验证。[Teamwork][T] |

**[DOC]** Integrity mode 写入获批 prompt。development 默认允许复用代码、库和框架，标记伪造结果或只有外壳的实现。demo 再禁止抄用开源核心逻辑、把核心工作委托外部工具、读测试源码反推预期。benchmark 要求从零独立实现，仅用语言标准库。三者回答的是项目允许哪些捷径，Auditor 据此核验。**[UNKNOWN]** 官方未公开自动发现全部 mock 的算法、证据签名或对误判的保证。[Teamwork][T]

**[DOC]** Request Artifact 保存目标、约束和验收标准；Project Plan Artifact 保存路线、轨道与依赖；Progress Artifact 保存当前里程碑和已完成轨道。计划与进度工件连同会话日志记录状态变化、命令、评审和验证。fresh successor 在里程碑之间承接管理，目的为缓解上下文退化。**[INFER]** 工件为接手者提供重建依据。**[UNKNOWN]** 必须读取哪些修订、如何原子提交、过期如何拒绝、中途崩溃如何从最近副作用恢复，均无公开契约。[Teamwork][T]

## 6. Schedule、Automation 与 Sidecar

| 维度 | 会话内 /schedule [S] | 跨会话 /automation [A] | 通用 Sidecar [D] |
|---|---|---|---|
| 生命周期 | 当前 conversation 创建的后台任务；创建后结束当前 turn，可继续聊天 | 定义跨 session 和 restart，初始 Paused，开启后周期运行，可 Run Now | 本地或插件提供的后台进程，显式启用后由宿主管理 |
| 触发目标和上下文 | 高优先级通知唤醒现有 Agent，保留对话历史 | 每次创建全新 conversation，无上次运行记忆 | 命令本身无需 Agent；agentapi 可创建新会话或给指定会话发消息 |
| Prompt | 可引用当前任务 | 自包含，交代目标、来源、输出；可参数化工作区 | 普通进程无此要求；自行建会话时由调用者供给 |
| 权限 | 当前 conversation 权限 | 可在当前对话实际试跑一次，记录精确访问到的 agent_permissions；可跳过试跑后人工检查。无人值守的待审批行为自动拒绝 | 普通进程按宿主配置运行；文档单列 automation 的 workspace_uris/access_grants，未给所有 sidecar 通用的 Agent 授权继承规则 |
| 存储 | 会话工具任务；磁盘格式与恢复语义未公开 | 全局 sidecars 目录下按 automation ID 保存 sidecar.json | 全局或插件 sidecars 目录，目录也是命令 cwd；sidecar_data 按 ID 分 data、logs、events |
| 重启与用途 | 当前任务内等 CI、回查部署；未承诺跨会话补发 | 定期新建报告或巡检。定义持久，错过某次是否补跑另计 | 选择 always（默认）、on-failure 或 never；schedule builtin 要求 always，其余两者用于一次性命令。进程重启不能证明一次 Agent Run 的结果 |

官方比较与配置见 [Schedule][S]、[Automation][A]、[Sidecars][D]。自动化配置位于 `~/.gemini/config/sidecars/<id>/sidecar.json`，插件定义在 `~/.gemini/config/plugins/<pluginName>/sidecars/`；开关在全局 config.json，运行数据在 `~/.gemini/antigravity/sidecar_data/<sidecarId>/`。Sidecar 通过 agentapi 开新对话须设 projectId。[Sidecars][D]

**[DOC] Timer。** DurationSeconds 指定等待秒数，Prompt 为超时唤醒指令。TimerCondition 默认 never，也可选 any 或指定 subagent/后台任务 ID。匹配消息先到会取消 timer，由该消息本身唤醒；没有匹配事件才到时发 Prompt。**[UNKNOWN]** 同时到达时产品内部的仲裁、重复投递或存储方式。[Schedule][S]

**[DOC] Cron。** 五字段表达式；每次唤醒的 Prompt 附迭代号。MaxIterations 缺省不限，达到上限通知完成。非 daemon 绑定发起 task，IsDaemon=true 可在该 task 结束后继续，仍受 conversation 范围约束。可让 Agent 列出或取消，或在 CLI /tasks 查看、按 k 终止、x 清理已结束条目、Enter 看详情。[Schedule][S]

**[DOC] 持久作业。** /automation 在 App 提供，用五字段 cron 和 IANA 时区；实际起跑可浮动最多周期的 10%，且不超过 15 分钟。该数字是 automation 的产品描述，不能推广为所有 timer 的时限或送达 SLA。**[UNKNOWN]** missed trigger、并发 overlap、dedupe、backoff、租约及外部副作用幂等没有在所查文档中定义。[Automation][A] · [Features][F]

## 7. 长任务中断与任务接力

| 情形 | Antigravity 的证据 | ZaoFu 应采用的判断 [INFER] |
|---|---|---|
| 执行遇到语义偏差 | [DOC] 同会话还给 Planner 修订。[Models][M] | 局部步骤可返修；改合同、target 或权限须返回原有审批和新版本路径。 |
| 到达里程碑边界 | [DOC] fresh Project Orchestrator 从工件接班。[Teamwork][T] | 保存获批范围、完成证据与未决项，为新承接者重新核对绑定。 |
| Worker 被移除或配额暂停 | [DOC] 前者换人，后者需外界唤醒。[Swarm][W] | 区分重新派 attempt、等待依赖与继续有效 session，各自记录原因与预算。 |
| Agent/控制器中途死亡 | [UNKNOWN] 无公开的跨系统提交顺序或副作用重放承诺。[Teamwork][T] · [Sidecars][D] | 先查已准入结果和 effect receipt，再决定继续、受控重试或给 owner 分诊。 |

## 8. Workspace、并行写冲突与集成

**[DOC]** Swarm 默认共享，可选 per-worker 副本；Teamwork 默认在 `~/teamwork_projects/{PROJECT_NAME}` 下做项目，Project Orchestrator 分配独占文件。各 subagent 的 scratch 保存脚本、笔记和日志。文档没有承诺为每个长期 Role 永久持有产品 worktree；独占描述也没有给出跨目录改名、生成文件或最终 merge 的算法。[Swarm][W] · [Teamwork][T]

**[INFER]** ZaoFu 可以公开各任务写入范围，保留已有按 Run、Task、Map、workspace generation、base 计划工作区的实现。若两个任务影响同一接口，即使改不同文件也需集成验证。目录回收沿用“任务终止、已集成/归档、无活动 attempt/session、无未提交修改”等检查。Candidate identity 已绑定输入、环境、质量信息，验收须定位到实际目标。[工作区计划](../../../src/zf/runtime/task_workspaces.py#L81) · [清理条件](../../../src/zf/runtime/task_workspaces.py#L245) · [Candidate 身份](../../../src/zf/runtime/candidate_integration.py#L25)

## 9. Verification 与 Readiness

**[DOC]** Swarm Verifier 把问题送回作者；Boost 改进者可自己修再重测，主编排者最终抽查。Teamwork 把评审、挑战、证据审计、最终 E2E 分开。**官方口径差异**：Teamwork 角色段称 Success Auditor 为 “final reviewer”，横向表把四角色一并描述为 “verify each milestone”。两处表述无法确定每个中间里程碑都启动最终审核。普通 Subagents 的 peer 直连与 Swarm 的 board-only 规则也必须注明适用拓扑；用户直达 Worker 是另一条入口。[Teamwork][T] · [Boost][B] · [Swarm][W] · [Subagents][U]

**[INFER]** ZaoFu 可预先确定独立验证义务、目标 commit、证据类型与 owner，按已准入结果计算仍缺哪道门。语义评价留给相应角色。真实命令 exit 0、角色自报完成、独立审核通过、Goal 通过各自需要证据。已有 TaskAttempt readiness 检查用于机械推广评估，产品判词应走 integration/goal 现有入口。[机械 readiness](../../../src/zf/runtime/task_attempt_readiness.py#L26) · [集成结果准入](../../../src/zf/runtime/integration_acceptance_result.py#L66) · [Goal 身份](../../../src/zf/runtime/goal_closure_identity.py#L25)

## 10. 统一能力矩阵

格内是各专页公布的行为 [DOC]；问号为 [UNKNOWN]。最后三行将容易混淆的可靠性保证单列。[Models][M] · [Boost][B] · [Swarm][W] · [Teamwork][T] · [Schedule][S] · [Automation][A] · [Sidecars][D]

| 维度 | 双模型 | Boost | Swarm | Teamwork | Timer/Cron | Automation | Sidecar |
|---|---|---|---|---|---|---|---|
| 单元、控制交接 | 同对话 Planner↔Executor | 主→协调→首轮/改进者→主 | 当前协调者→具名成员 | Sentinel→项目管理者→各轨道与审核者 | 现有会话被唤醒 | 按次新建对话 | 执行进程/内建作业 |
| 上下文、工件 | 读本轮状态与计划 | 原任务及相关上下文下发，子上下文隔离 | 清洁子会话、公开 brief/板 | 已批请求、计划、进度、scratch、日志 | 原有历史 | 自包含 prompt、参数 | 文件/进程自管，可调用 agentapi |
| 工作区与并发写 | 执行者改当前项目 | 共用项目、隔离上下文 | 共享或独立副本、分工 | 项目专属目录、独占文件 | 原会话权限 | 配置的工作区与授权 | 自身 cwd 和 data |
| 验证与语义异常 | 自测、偏差返规 | 挑战/修复/复测、主抽查 | Verifier 回作者 | 与完整性模式匹配的分层验证 | 按唤醒任务处理 | 按独立 prompt 处理 | 无通用语义门 |
| 配额异常 | ? | ? | 暂停，额度刷新不主动重启 | ? | ? | ? | 无通用 Agent 配额语义 |
| 持久性、崩溃恢复 | 会话中可续，崩溃契约 ? | ? | 移除永久，崩溃恢复 ? | 工件计划性交棒，中途崩溃 ? | 限会话，崩溃补发 ? | 定义跨重启，单次执行恢复 ? | 可重启进程、持久目录 |
| Lease、超时接管 | ? | ? | Monitor 只见全体静默 | ? | 到时通知，业务接管 ? | ? | 重启策略，业务 lease ? |
| Retry、幂等 | 语义返规，业务幂等 ? | 再委派，副作用去重 ? | 修复、替人，幂等 ? | 里程碑再验证，重放 ? | 事件与 timer 竞争仲裁 ? | 漏触发/重叠/退避 ? | 进程策略，语义 Retry ? |

## 11. 官方文档未回答的问题

- **[UNKNOWN]** 谁持有持久写租约，旧 owner 如何被隔离？一个准入结果或外部操作已落地、Agent 尚未来得及报告时怎样对账？[Swarm][W] · [Teamwork][T]
- **[UNKNOWN]** 公共板是否持久回放，投递是否至少一次，有没有每个接收者的去重水位？换工作副本是否强制精确 commit 验证、如何返工冲突？[Swarm][W]
- **[UNKNOWN]** 工件 revision、hash、跨工件原子性、旧交接失效条件、中途崩溃恢复点及实际门禁编排频率。[Teamwork][T]
- **[UNKNOWN]** 持久作业的错过触发、夏令时歧义、重叠、补跑、去重、退避，以及任务启动与外部效果之间的事务边界。[Automation][A] · [Sidecars][D]

## 12. 对 ZaoFu 的 P0 / P1 / P2 建议

| 优先级 | 借鉴与改造 | 可判定的放行条件 |
|---|---|---|
| P0 | 直接借鉴公开分工、事先约定验收、短交接；在现有 operation、工作记录、task workspace、Candidate/Goal 准入上实现 milestone successor。 | 新接手者获得精确输入与剩余义务；旧 attempt 的迟到结果不前推；恢复无需重复已确认副作用。 |
| P1 | 借鉴 board 和事件/超时唤醒。消息带身份与证据引用，沿既有 ingress、去重、受控 continuation 派送。 | 同一事件投递两次、事件与 timer 同时到达、控制器重启，均只形成一次合法接续。 |
| P2 | 独立订阅持久作业；在单 Worker 内试验双模型返规；按风险选择额外审查者。 | 新对话使用冻结授权，按 occurrence 对账；模型切换不扩大写权限。费用和收益按真实任务单独评估。 |

[INFER] 不移植长期 Role 占用项目目录、用板帖批准、单凭进程重启判定业务成功或每个任务固定增加四名 reviewer。具体接口、重放与反例见[配套规格](213-zaofu-antigravity-borrowings.md)。

## 13. 官方来源索引

以下九页于 **2026-11-23 UTC** 直接获取官方正文。来源链接指向页面，标题后的章节名可在页面内定位。

| 官方标题 | 主要核对章节 |
|---|---|
| [Choose a model][M] | Orchestrator |
| [Swarm (/swarm)][W] | How it works；Worker models and workspaces；Member states；Steer a swarm |
| [Teamwork agent teams (/teamwork)][T] | Multi-agent architecture and roles；Two-phase workflow；Integrity modes；Safety, workspaces, and isolation |
| [Schedule command (/schedule)][S] | One-shot timers；Recurring cron schedules；Manage active schedules |
| [Automation command (/automation)][A] | How /automation works；/automation compared with /schedule |
| [Sidecars][D] | Config schema；Runtime data；schedule builtin；Automations；agentapi |
| [Custom subagents][U] | Subagent lifecycle and states；Inter-agent communication；Multi-agent orchestrators |
| [Boost deep reasoning (/boost)][B] | How Boost works；Safety and permissions |
| [Feature overview][F] | Automations，核对时间浮动上限 |

[M]: https://antigravity.google/docs/models/#orchestrator
[W]: https://antigravity.google/docs/swarm/
[T]: https://antigravity.google/docs/teamwork/
[S]: https://antigravity.google/docs/commands/schedule/
[A]: https://antigravity.google/docs/commands/automation/
[D]: https://antigravity.google/docs/sidecars/
[U]: https://antigravity.google/docs/subagents/
[B]: https://antigravity.google/docs/boost/
[F]: https://antigravity.google/docs/features/#automations
