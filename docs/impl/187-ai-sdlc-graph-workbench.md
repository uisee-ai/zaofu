# 187 Workflow 图工作台实施与验证

> 状态：本批实施与真实端到端验收完成。设计入口：[187](../design/187-ai-sdlc-graph-workbench.md)。

## 实现边界

采用 **skills 提供方法、Agent 输出语义关系、代码校验并渲染事实** 的方式。Planner 在现有 Task Map 中声明 `modules`、条款与任务引用；模块不会改变调度，也不会复制 Task。不同项目共享相同 schema、投影和 React Flow 组件，无产品名称、业务目录或任务类型的硬编码布局。

`WorkflowProposalPage` 内提供 Lifecycle、Task、Dag、Trace、Delivery、Timeline 六个视角。结构来自 Proposal 固定的编译配置，动态叠层来自当前 Request/Run 的 EventLog、TaskStore、Workflow Operation、Fanout、Attempt 和已绑定计划工件。未声明模块、缺少证据和未接入生产环境均明确显示，不补造成功节点。

## 入口与合同

| 能力 | 实现入口 | 验证 |
| --- | --- | --- |
| 模块身份、共享任务、条款引用 | `runtime/plan_modules.py`、`task_map.py`；`skills/zf-plan-task-map-contract/references/module-graph.md` | `test_plan_modules.py`、Task Map 调用方闭包 |
| 已批准路线结构快照 | `runtime/workflow_graph_snapshot.py`、`workflow_proposal.py` | `test_workflow_graph_snapshot.py`；跨路线与外部事件边界 |
| Run 隔离与历史游标 | `web/projections/workflow_workbench_sources.py` | `test_sdlc_graph.py`；外来 Run、相同 Task、当前配置变化、历史状态不冒用 |
| 统一图与动态执行 | `runtime/sdlc_graph.py`、`sdlc_graph_timeline.py` | 共享节点、Fanout 子 Agent、失败返工、未覆盖条款、队列等待、并行最长路径 |
| 图 API | `GET /api/projects/{project}/workflow-requests/{request}/graph?as_of=0` | 真实 Router 测试，读操作不追加事件 |
| 完整 YAML 后局部修改 | `runtime/workflow_preview_review.py`、`workflow_review_fields.py` | `test_workflow_preview_review.py`；lane/model/skills/Plan 审批、自动选路、并发准备租约、陈旧绑定 |
| PRD/Plan 局部修改 | `runtime/plan_artifact_review.py`、`web/plan_artifact_review_routes.py` | `test_plan_artifact_review.py`；原文摘要、Unicode 引用、不可变候选、受控重新规划、token gate |
| Workflows 交互 | `WorkflowGraphWorkbench.tsx`、`PlanArtifactChat.tsx` | 构建、图模型测试、Docker Playwright |

## 187 条款核对

| 设计范围 | 本批落点与验收依据 |
| --- | --- |
| §2/4 六视角及生命周期 | Workflows 内共享画布与 Inspector；Docker Playwright 遍历六视角 |
| §5 PRD 模块、共享任务、依赖 | 同一 Task Map 的 `modules` + `goal_claims`；共享 Task、多父 DAG、未知引用、真实计数器模块 |
| §6 配置结构与运行 | 固定 Proposal 编译图；Issue/Prd/Refactor/Workflow 四类编译测试；真实 Scan 并行、Plan、返工、实现 |
| §7 Agent 与协作 | Operation/Fanout/Attempt 与工具摘要；实际 agent.usage 的 Provider/model，缺失时标注配置快照 |
| §8 追溯与变更 | 有类型关系、上下游、版本游标、不可变 Plan/YAML 候选；结构可达与待评估业务影响分开 |
| §9 验证与交付 | 当前代内核接纳、Goal Coverage 旧证据校验、候选/Ship/生产状态分开；未连接生产数据源明确未接入 |
| §10 时间线 | 实际请求/执行时间、排队与区间并集、DAG 最长路径；数据不足不预测 ETA |
| §11 权威与历史 | EventLog/Store/固定工件只读投影；不另建调度状态；历史缺失显式诊断 |
| §12 交互和规模 | 搜索/状态/关系筛选、折叠、稳定 ID/位置、节点链接、1200 节点夹具；完整快照，无服务端邻居分页 |
| §13 用户 a/b/c | 两个浏览器创建项目；真实问答/YAML Chat/Plan Chat 与动态观察；实际终态见运行记录 |

## 操作流程

1. 在 Kanban Agent 输入需求并回答澄清问题，选择准备 Workflow。CLI / Channel 创建的同类 Workflow Request 使用同一投影入口。
2. 在 Workflows 顶部选择 Workflow，在 Dag 点击 Raw 审阅完整 YAML；点击 YAML 面板内的 Agent 展开小回复框，用自然语言讨论或修改并行数、模型、角色 skills，Agent 自动定位字段、必要时追问，检查 Diff 后应用生成新版本。
3. 待澄清请求通过 Answer Questions 处理，候选在 Raw 审阅。需要先审阅计划时，在 Chat 中要求打开“计划生成后等待审批”。点击 Start Workflow 后依序应用必要的候选配置并提交当前 Proposal；任一步失败即停止，runtime 仍由既有启动链自动拉起。
4. Scan/Plan 生成已绑定文档后，点击 `PRD / Plan Preview`。在 Agent Chat 原文区域选择片段，提出修改并检查候选全文，确认后走既有 Plan reject / replan 决策路径。
5. Planner 重新提交计划后再批准实施。在 Workflow 执行视角观察阶段、Agent、尝试和返工；从模块/任务进入追溯与验证视角核对提交及证据。

局部修改不等于批准。配置修改始终重新编译、生成新 Proposal；文档修改生成不可变 sidecar，原 Plan 不原地覆盖。已处理的计划不能用旧草稿再次修改；执行中的变更必须经既有受控重新规划入口。

## 图优先页面与主题（2026-10-26）

取消 Needs decision / Active / History 页签和常驻请求列表；顶部保留 Workflow 标题；存在多个请求时通过选择器切换，选项显示请求 ID、类型、状态和修订；单请求不显示选择器；不再单设类型/修订栏或 Review & Approve 上下文栏，深链接继续绑定 request/graph/node。默认展示 Graph，Graph 与 Timeline 是主要入口，Lifecycle、Task、Trace、Delivery 在「更多视图」中；各视角共享请求与节点身份，旧链接仍可打开。未生成配置的请求仍显示澄清和准备入口，不要求已有 Run 才能访问。

六个视角默认铺满页面剩余空间，无需先点击展开。画布高度受实际内容容器和窗口共同约束；窗口或侧栏尺寸变化后自动适配节点，宽画布自动适配至多 80%；窄画布自动适配最低 65% 以保持节点可读，Fit View 仍可缩小至完整可见；移除底部的版本与数据可用性、Live Events 区块，节点详情仍保留来源与执行证据。Timeline 按阶段显示已观测的操作区间，不展示缺少依据的关键路径或预测；Trace 和 Delivery 移除常驻说明文案，节点与受控接口的证据规则保持不变。画布采用纵向分层、圆角卡片和带箭头的曲线虚线。Provider 从已有执行绑定读取，以卡片内的颜色和文字区分；卡片采用中性细边框，执行状态单独使用文字和图标，不能从 Provider 颜色推断成功。目标节点使用统一圆角。背景、卡片、文字、连线、缩略图和控件共享现有明暗主题 token，不固定黑底。活动状态与连线随同一只读快照更新；普通轮询保留位置与缩放，主动筛选重新布局；减少动画偏好关闭运动效果。

审批与运行控制统一从 Raw 面板进入；需要澄清的请求仍在顶部提供 Answer Questions，Plan 待审提示继续保留。Raw 是绑定 Proposal 的完整脱敏 YAML。未启动的 `proposed` 以及有当前不可变阻塞候选的 `ready` 请求均可使用 Agent Chat；阻塞候选用自己的 digest 绑定，修改后重新编译，有剩余阻塞项时保持不可审批。刷新时从匹配当前修订的准备候选或已记录的修改结果恢复 YAML，不修改 canonical `zf.yaml`，不自动启动 Run。Raw 专注展示 YAML；图工具栏不再显示 Agent，YAML 下方原 Details 位置改为 Agent，展开面板内的小回复框。没有居中模态窗口或背景遮罩；初始只显示一个输入框，消息和 Diff 按需出现在框内，支持 Ctrl/⌘+Enter 发送、Apply/Discard、Esc 或点击框外收起。Details 入口移除；必要的 Answer Questions、Prepare Workflow 和 Run Controls 入口继续保留。Chat 产生待应用候选期间不能切换请求或启动 Workflow；可关闭对话框，再打开时保留当前页面内的对话、输入及待应用 Diff。关闭 Raw 也不销毁 Chat；切换项目或请求后使用独立上下文。选中 YAML 片段可以引用到回复框，作为当前版本的修改上下文，由 Agent 定位可编辑字段并生成 Diff。应用生成新 Proposal 和图；Start Workflow 是一次显式启动确认，顺序调用原配置应用及提交接口，绑定相同 Proposal digest，失败不继续提交。已应用配置而提交失败时，刷新后重试仅提交，不重复应用。YAML 面板内的 Agent 入口始终可见；已启动 Run 时允许基于冻结 YAML 提问讨论，不生成可应用的 Diff；尚未生成可读预览或缺少 Agent 调用权限时，对话框显示具体原因并禁用输入及发送；历史事件回放时禁用 Raw，返回实时后可检查当前 Proposal，避免把当前配置误作历史配置。

专用 Prompt `src/zf/runtime/workflow_dsl_prompt.py`（`workflow-dsl-editor.v1`）由 Preview 服务实际传入 headless 调用。输入包含服务端读取的当前脱敏 YAML、版本绑定和可编辑字段，Agent 负责判断讨论、追问或修改；代码只检查字段白名单、类型、版本、编译及受控应用。`mode=chat` 不要求选择 field，返回 `answer` 与 `changes:[{field,replacement}]`；支持一次修改多个配置字段，或一个需求字段，禁止混合。需求字段的结构化引用修改仍仅替换选中片段；YAML 引用通过消息提供上下文，配置变更仍受字段白名单和编译校验约束。headless 使用 read_only 权限，不直接写配置或启动；完整 YAML 通过已有 immutable snapshot 引用记录在事件中，不重复写入 ledger。旧 discuss/revise API 协议保留。

运行后的 `context` / `discuss` 可读取同一 Proposal 绑定的冻结快照，返回 `read_only=true`，并将全部字段标为不可编辑。历史 YAML 恢复与脱敏共用 `runtime/workflow_snapshot_yaml.py`；不以当前 `zf.yaml` 替代运行快照。Agent 可以回答问题，代码拒绝 revise、非空补丁及旧候选 Apply。Provider 返回后再次校验版本，启动前发起的请求若期间已经启动仍拒绝迟到结果。此路径继续要求 action token；缺少权限时优先解释权限原因。API、浏览器与真实 headless 回复证据见 [运行快照 Agent 对话](../records/2026-10-27-0245-workflow-snapshot-chat.md)。

旧版 Workflow 的 effective-config 快照可能只有结构化 `config`，没有 `yaml`。详情投影先验证不可变快照，再从同一 Proposal 的 private candidate 恢复完整脱敏 YAML；候选路径、大小和内容摘要均须通过检查，摘要必须同时匹配快照来源及 Proposal 目标。候选不可用时仅序列化已验证的冻结配置，并明确标为 `Compiled configuration snapshot`；快照本身不可读时显示证据不可用。不读取当前项目 `zf.yaml` 作为历史内容，也不改写旧快照、Proposal 或事件。实际 tt 修复及浏览器证据见 [旧版 Raw YAML 兼容记录](../records/2026-10-26-1540-workflow-legacy-yaml.md)。

PRD/Plan 待审时在 Live 状态同一行右侧提供 Review 入口。主阅读区直接显示服务端绑定的文档，Preview 支持选段引用，Raw 支持精确选字。选中内容后才出现「引用到 Agent」悬浮入口，正文没有逐段编辑按钮。格式化内容按 Markdown 语法树的源区间引用完整段落/列表/表格块，保留标记；Unicode 偏移转换后送入原审阅合同，重复文本不做全文搜索匹配。审批概览与附件折叠保留为只读资料，加载失败明确提示并可重试。引用打开与 YAML 共用 `PreviewAgentReply` 的小回复框：一个输入框与 Send，按需显示消息、引用和 Diff；支持 Ctrl/⌘+Enter、关闭重开保留内容以及 Apply/Discard。点击外部动作后再收起，并使用原始事件路径识别已被 React 移除的 Diff 按钮和引用入口，避免吞掉点击或误收起。主阅读入口的早期修复证据见 [Plan Review Entry](../records/2026-10-26-2329-plan-review-entry.md)。

Plan 审阅的 `mode=chat` 由 headless Agent 判断讨论、追问或局部修改；原 `discuss/revise` 协议保留。候选必须绑定同一 Plan、文档摘要与 Unicode 引用区间，引用外正文由服务端保持不变；Provider 返回后重新检查计划是否仍待审批。Apply 通过原受控拒绝/重新规划入口提交不可变候选，不批准旧计划或新计划；待应用 Diff 与重规划状态同步前锁住批准操作。已退回版本说明正在等待新版。Workflow 选择器优先显示 Synthesis 已选的 Flow family，未决时才显示原始 `auto`。页面没有新增调度或审批状态机；Kanban Agent、Channel 与 CLI 提交接口未改动。真实 Kanban → PrdFlow → 人工预览 → 引用修改 → 新版待审验证见 [ty Plan Chat](../records/2026-10-26-1700-ty-plan-chat.md)。

选区悬浮入口跟随选区位置并限制在窗口内；滚动、缩放、清空选区或 Esc 将其收起。只有点击「引用到 Agent」才替换对话中的引用，临时选中文字不改写已有引用。Agent 入口与 Preview / Raw 位于同一工具栏，回复框锚定在工具栏下方，不推移正文，也不再出现在文档底部；窄屏保持入口同排并限制回复框宽度。待应用候选或 Agent 调用期间不开放新的引用入口。桌面真实鼠标选择、headless 修改及手机布局验证见 [2026-10-27 选区引用](../records/2026-10-27-0128-plan-selection-quote.md)。

Inbox 的 Plan 预览与列表也提供「引用编辑」，进入相同的 `PlanArtifactChat`；共享样式由编辑组件加载，不依赖先访问 Workflow。该入口不再把修改要求发往 `chat-orchestrator`，避免没有配置 orchestrator Agent 时仅得到 `queued_no_runtime`。默认审阅视图和附件阅读保留；编辑时选段调用 headless Agent，再通过原 Apply 请求重新规划。候选存在或调用进行中时锁住 Approve、Reject、关闭和返回审阅；Apply 成功重新读取原 Plan 状态并刷新 Inbox，已退回版本不能批准。Discard 只丢弃页面候选，不写 Plan。Agent 工具栏定位与真实引用修改验证见 [2026-10-27 工具栏回复框](../records/2026-10-27-0147-plan-agent-toolbar.md)。

Inbox 与 Workflow 入口的真实 Agent 修改、Apply 后重新规划及新版人工待审闭环见 [2026-10-26 Inbox Agent 验证](../records/2026-10-26-2343-plan-inbox-agent.md)。

引用编辑默认使用格式化 Preview，沿用 Plan 阅读页的标题、行高、代码换行和加粗样式；段落间保持文档间距。长表格与代码块复用 `planMarkdownPages` 分段渲染，避免因单块过长降级为纯文本；渲染时补充的表头不改变原始 Markdown 源区间，引用仍绑定原文与摘要。

Inbox 引用编辑时正文占满工具栏下方的宽高，资料侧栏在返回审阅后恢复。Workflow 的 Plan 也使用全屏阅读窗口，审批按钮保留在顶部，辅助资料折叠在底部。两个入口的正文都填满剩余空间，取消 55dvh 上限，独立滚动；文档选择、Preview / Raw / Agent 保持可见。早期高度修复见 [阅读空间对齐](../records/2026-10-27-0159-plan-edit-reader.md)，本轮格式化与两个入口全屏验证见 [统一阅读模式](../records/2026-10-27-0230-plan-reader-unified.md)。

该实测随后暴露 Autoresearch 子进程继承父项目路径环境的问题。实验命令必须清除 `ZF_PROJECT_ROOT` / `ZF_STATE_DIR`，从自身 cwd/zf.yaml 解析状态；否则 `init --force` 和 `stop` 会作用于父项目。隔离修复、父账本保护回归及历史 ty 数据受损情况见 [2026-10-27 故障记录](../records/2026-10-27-0016-autoresearch-context-isolation.md)。

窄屏的项目导航行按实际内容取高，避免空白导航区挤占画布。

浏览器验证使用 `tests/e2e/scripts/workflow-prepare-server.py` 的隔离目录、独立 workspace 和 Docker Playwright。Provider 与启动进程为确定性替身；图实时状态及 Plan 面板测试明确使用投影夹具，不作为真实 Agent 执行证据。相关用例见 `web/tests/workflow-graph-redesign.spec.ts`、`workflow-preparation.spec.ts`、`workflow-preview-review.spec.ts` 和 `workflow-proposal-page.spec.ts`。

Raw 阻塞候选的真实 Codex 验证见 [2026-10-26 Raw Chat 修复](../records/2026-10-26-1342-workflow-raw-chat.md)。 自然语言 DSL 编辑、单次启动确认及真实多轮 Chat 证据见 [2026-10-26 DSL Chat](../records/2026-10-26-1428-workflow-dsl-chat.md)。

改版验证与截图见 [2026-10-26 验收记录](../records/2026-10-26-1218-workflow-graph-ui.md)。 简洁 Agent 对话框验收见 [2026-10-26 Agent Dialog](../records/2026-10-26-1453-workflow-agent-dialog.md)。 最新 YAML 内小回复框及 Details 移除验收见 [2026-10-26 YAML Reply](../records/2026-10-26-1524-workflow-yaml-reply.md)。

## 状态与数据限制

- Workflow 首页展示 Goal 上下文及 Graph / Timeline 两个主要入口；Lifecycle、Task、Trace、Delivery 放在「更多视图」，旧 `graph` 深链接继续可用。默认 Graph 只画已编译的阶段和终态节点，配置角色显示为配置摘要，运行细节按阶段下钻。阶段间折叠连线仅沿已有编译 Workflow 路径生成并明确标记，未声明的连接保持缺失；具体触发条件需要展开完整结构。其他大图默认限制可见节点，明确提示折叠可能造成关系不完整，并允许展开。
- 标准阶段使用明确的展示别名，自定义标题与未知阶段保留原文和节点身份；Judge / 最终验收和 Verify 是不同阶段。终态节点改为紧凑门禁，仍显示原投影状态并可打开证据，不能由阶段完成推断门禁通过。Goal 长文本默认截取原文，展开可看完整内容。标准验证后扫描在 `pending` 时以名称和状态收进「其他阶段」，可展开且旧节点链接仍可定位；执行、失败、阻塞及未知状态保留在主图。被折叠的阶段不代表可跳过，折叠路径不会穿透该隐藏阶段补造连接。
- 阶段详情在结构快照中仅显示已配置角色，并按需加载完整运行图；加载后按 Operation 的 `parent_stage_id` 展示已观测操作和 Task，不把关联计数表述为完成/验收。Timeline 按阶段展示实际 Operation 的 Task、执行者和已观测区间，保留阶段及操作详情入口；缺少开始或结束时间时明确显示未知，不推算跨度。可在真实时间和明确标示的不等比例压缩空档之间切换。Loop Timeline 负责查看 Task 尝试及反馈循环。
- Loop 的 `timeline` 是附加只读投影：仅以带 Task 和 Attempt 身份的启动及匹配终态形成已观测尝试，非匹配记录保留为事件信号，计数是已观测下界。存在 Run 时，只采用身份可归属该 Run 的事件；旧业务视图对单 Run 的无归属事件兼容不用于新时间线。既有 `tasks` 和业务环卡维持原投影口径，可能包含 Fanout 成员，不能与新时间线数字直接比较。Workflow 与 Loop 的链接携带 Run 身份；当前 Loop 与目标 Run 不一致时明确提示，不把当前数据当作目标 Run。
- Plan vs actual 仅在当前范围事件含内核准入的任务清单引用、摘要与任务 ID 时比较事件所记录的任务身份；不重新校验引用文件，这不是耗时或成本基线，也不代表任务已验收。缺少可信时间及成本依据时不展示偏差、ETA 或成本预测。投影读取仍经 EventLog 与指标索引；为此所需身份事件从账本补充 payload，并受现有源指纹和缓存约束。

- Task 绿色完成要求当前代内的内核 `task.done.accepted`，或同 Run、同代次 `run.goal.completed` 中明确列出的 `completed_task_ids`，同时 canonical Task 已置 done；Worker 自述仅显示已报告。复用 Goal Coverage 的已接纳结果、目标提交和代次校验，过期验证不计入当前闭合。TaskStore 中的提交链接只提供追溯。
- 历史模式按事件游标读取，无法重建的 Task 历史状态显示未知。需求与配置取已绑定 Proposal，不重建旧准备修订；后续编辑当前 `zf.yaml` 不改写旧 Run 图。
- 下游影响表示已声明关系可达；未声明业务依赖仍待 Agent/人工评估。
- API 的时序分析只使用实际开始/结束/请求时间，缺少时长不估算 ETA；关键路径计算 Task 操作区间并集后的 DAG 最长路径，不累加并行任务。Timeline 页面不展示这些统计表格。
- 当前未配置部署/生产健康数据源，发布与生产健康保持独立；代码交付不等于生产健康。
- API 返回完整快照与 total/included/truncated 统计；画布使用可见区域渲染和层级折叠，1200 节点夹具验证无遗漏。当前没有服务端邻居分页。
- 每 2 秒拉取一致快照，后台页面暂停轮询；返回实时后重新读取完整快照。请求有并发/范围保护。事件索引仍由 API 提供，页面不再单独展示 Live Events；节点活动详情中的大型工具输入/输出使用有标记的摘要，完整正文保留在 EventLog / sidecar。
- 切换视角、状态、搜索、关系范围、折叠或回放时，按当前可见节点重新布局并适配画布，避免少量活动节点沿用完整历史图的稀疏坐标。普通实时轮询保留节点拖动位置和缩放视角；只查看节点详情不重排。Plan Chat 的 Grid 列宽限制在面板内，完整文档中的宽表格不能撑开局部 diff。

## 真实验证记录

隔离根目录：`/tmp/zf-187-e2e-20261025T161931Z`；临时 Web：`0.0.0.0:8003`。
Docker：`mcp/playwright:latest`，host network；没有安装宿主浏览器。
浏览器创建项目 `counter-project-3014ef68`，使用自定义 state_dir `.zf-187-counter`。
需求为原生网页计数器；真实 Codex 执行 Kanban 问答、Workflow Synthesis 和 Preview Chat。

已观察并修复：

- Synthesis 混淆“启动前预览”和“最终仅交付预览”；目录现在区分已安装与实际角色绑定 skills，并提供当前完成条件。
- lane 数变化后保留旧 lane 角色，自动选路使用错误的编译 family，以及后台消费者与同步局部修订争用准备操作；已补充编译角色刷新、路线选择与共享租约。
- 配置密钥检查将 `task-map-contract` 中的 `sk-` 子串误判为密钥；修复词边界，真实 token 值仍阻止应用。
- 候选启动沿用 fanout 汇聚事件的因果与关联 ID；旧无 Run 字段的候选只沿已归属 writer 收据和因果链补入，混合其他 Run 的收据不归入本图。
- 阶段按事件顺序取最新 fanout 的汇聚判定；已结束的子 Operation 不覆盖整个阶段的失败或取消。
- 真实 `candidate.integration.completed` 的结果可能仍为质量失败；图按 `status/quality_status` 展示失败、过期或已通过，不能仅凭 completed 后缀点绿。
- 历史 Task 被重新绑定到另一个 Run 后，仍按事件的显式 Run 归属保留历史事实；不使用后来 Store 的绑定抹去旧记录。
- 真实恢复暴露 writer self-check 省略 authority 字段的兼容缺口；在已验证合同快照上补齐省略的机械身份字段，显式冲突仍拒绝；已先重现失败再修复。
- 已完成 writer fanout 的交接补偿不再将 lane 从 idle 改回 awaiting_review；使用既有结果绑定识别省略 fanout 字段的历史交接，普通任务审查流保持原有状态语义。
- 终态读取当前 Run/代次最新的内核 Goal Claim Set pin，避免准备阶段 Goal 身份遮住正式闭合结果；保留原始需求分母。Run 级明确任务验收可解释合法的 canonical done，不要求补造单任务验收事件。
- 取消、放弃、阻塞等终态事件保留自身判定，不能因事件已接纳而显示为成功。
- Mock Channel CLI 夹具 owner 与调用者不一致：在原 dev 重现后仅修正测试 owner，不放宽产品权限。

第二个浏览器新建项目为 `todo-project-3ef9b6f4`，需求为待办列表。自动选择 IssueFlow light，通过真实 Chat 修改 `roleSkillBundles.impl`，应用候选后重新加载实际 `zf.yaml`，确认 `issue-fix-lane-0.skills` 含 `zf-plan-task-map-contract`，且没有启动 Run。PrdFlow 使用编译前的 `planner` 键，多 Flow 运行角色前缀不作为配置键；无实际编译效果的 skills 修改会被拒绝。

主项目完成了真实 Plan 局部审阅：选中重置行为段落，改为可见“归零”按钮；确认不可变候选后触发受控 `plan.rejected`，Planner 读取修订工件并重生成计划，Critic 核验通过。浏览器再次读取新 Plan，全文与确认候选逐字一致，再确认计划并观察 `COUNTER-001` 进入执行。

真实运行最终完成：候选 `cd8a21c` 的四条原始验证命令、clean/diff 门禁通过；独立 Verify、Discovery、Judge 完成并被内核接纳。`run.goal.completed` 为 `evt-c227df403c7a`，任务正式 done，6/6 目标闭合，阻塞缺口为 0。原始 3 条需求没有直接的 Task Map 覆盖边，这一计划映射诊断仍保留；最终闭合来自受接纳的 Verify/Discovery/Judge 证据，不能把映射缺口隐藏掉。

本次交付是经过验证的本地候选及报告，没有生产发布。未配置 Feishu alert route，终态通知投递失败与软件验收结果分开记录。详细事件、截图、产物及分层结果见 [运行验证记录](../records/2026-10-25-187-workflow-graph-e2e.md)。

## 验证分层

- 后端影响闭包：249 项通过，覆盖 Task Map、Plan/Preview、Workflow 编译与配置应用、真实 Router、Goal Coverage 和双入口 Mock。
- 更新后的路线 skills 审阅专项：26 项通过，覆盖 Issue light/fanout 与 Prd 多 Flow 输入键及真实编译效果。
- 图、覆盖、旧证据与恢复链：最终 72 项通过，含 Goal Claim Set、终态正反例及确定性 recovery E2E；这不是 Provider 执行证明。
- Writer 交接及调用方闭包：180 项通过；省略 authority 字段的回归先在原实现失败，修复后省略值从已验证合同补齐，显式冲突继续拒绝。
- Writer lane 释放与普通交接恢复闭包：252 项通过；补充历史缺少 fanout 字段的识别后，普通交接与两条 fanout 恢复调用方 58 项通过。
- 候选因果链及扩展调用方：285 项通过；另 1 项因测试启动器 PATH 缺少 `python` 返回 127，加入 venv/bin 后该项重跑通过，未改产品代码/断言。
- 自动验证器：Web typecheck、全部 Web 模型测试、51 项 sentinel、10 项 flow-smoke 与 manual docs currentness 通过。构建及 bundle budget 通过。
- Host + Mock 自动启动：3 项通过；临时 runtime 与端口隔离。
- Docker Playwright：最后 3 项通过（34.6 秒），包括真实 Run 的六视角/链接/移动布局、真实 Run/Task/Goal 终态与内核证据一致性，以及明确拦截 API 的长段落 Chat diff 夹具。执行期间另有实时事件更新断言通过。
- `dev-verify` 的文件数阈值将本批扩为 broad deterministic；依据 AGENTS 的验证分层要求，显式执行上述影响闭包，自动运行器跳过 broad deterministic/serial。没有运行全量 pytest，也不将 focused 结果称为全量验证。
- 改动的 skill 源和两份分发副本逐字一致。通用 skill 校验器不认识既有的 ZaoFu `stages/tags/auto_inject/load_on_demand` 扩展；这些 frontmatter 未修改，另按仓库合同检查引用和分发一致性。

关键回归命令（在本 worktree，`PYTHONPATH=src`）：

```bash
.venv/bin/python -m pytest tests/test_sdlc_graph.py tests/test_workflow_graph_snapshot.py tests/test_goal_coverage_graph.py tests/e2e/test_doc180_currentness_mock_e2e.py tests/e2e/test_recovery_control_loop_serial.py -q --no-cov
.venv/bin/python -m pytest tests/test_writer_contract_handoff.py tests/test_impl_self_check.py tests/test_writer_fanout_runtime.py tests/e2e/test_doc180_currentness_mock_e2e.py tests/e2e/test_recovery_control_loop_serial.py -q --no-cov
.venv/bin/python -m pytest tests/test_workflow_preview_review.py -q --no-cov
.venv/bin/python scripts/dev-verify.py plan --base dev --tests tests/test_plan_artifact_review.py --tests tests/test_sdlc_graph.py
.venv/bin/python scripts/dev-verify.py run --base dev --tests tests/test_plan_artifact_review.py --tests tests/test_sdlc_graph.py --skip-tier deterministic --skip-tier serial
bash scripts/dev-premerge-gate.sh
```

各层存在重叠，不将以上计数相加作为独立用例总数。结果收据见 [验证结果](../records/187-workflow-graph-e2e/verification-results.json)。

## 赛车监工续测中的入口修复

后续 [赛车真实验收](../records/2026-10-25-2110-racing-supervisor-e2e.md) 发现，准备 Worker 只观察默认项目和访问过 Workflow 路由的项目。现在后台按 workspace 注册表持续发现新项目，读取各项目配置指定的 state_dir，消费已受控入队的 Synthesis；无需先打开 Workflows 页面。原有最多两个项目同时消费、进程租约和事件增量检查保持有效，注册项目本身不创建请求或启动 Run。

Kanban/Channel 共用的合同路径形状判断保留 `.gitignore` 等根目录 dotfile；后续 canonical 合同路径校验继续拒绝 runtime/internal、绝对路径与目录穿越。

授权 Workflow submit 后，runtime 进程仍存活但超过同步等待窗口时，回执保留已接纳请求并返回 HTTP 202 / `runtime_start.status=starting`，不写启动失败事件，也不声称 ready；观察到退出或启动异常仍返回失败。准备 Synthesis 的 `workflow-request:*` 操作身份不再作为产品 Run；当前需求修订的准备取消显示在 History，后续新修订可重新准备。

验证命令的路径提取对 Docker `--pull never` 与 `--pull=never` 保持一致：镜像拉取策略值不作为镜像名，挂载目标和镜像标识不作为仓库输入；镜像后的项目脚本仍需声明路径归属。真实 Planner 首轮提交触发此误报，回归先复现失败后修复，未放宽任务路径或 assembly owner 约束。

Plan Preview 从绑定的 source index 补读可选 `authority_refs` 时，仅消费列表。字段缺失、null 或其他形状表示没有可展示的附加引用，不再令 Plan/Inbox/Workflow 图接口崩溃，也不将字符串拆成字符引用；原 Plan 正文、摘要校验和待审批状态保持不变。

引用编辑默认选择已绑定的完整 Markdown Plan。失败任务恢复时，Kernel 从同一 workflow、已批准 Plan、writer fanout 和失败事件的来源链继承正文引用；任务表、需求和计划包身份必须一致，正文与任务表摘要必须校验通过。对已生成的旧恢复事件，预览投影按同样规则还原引用。无法验证全文时摘要仅供阅读，不能将摘要当作全文交给 Agent 修改；其他附件由用户主动选择。Apply 沿用受控退回和重新规划，切换文档后默认恢复 Preview。验证见 [全文引用修复](../records/2026-10-27-0322-full-plan-review.md)。

Provider 的结构化文件写入保护复用现有目标解析器，只检查实际 Add/Update/Delete/Move 或 Write/Edit 目标，避免把规划文档正文里的账本引用当作写账本。未知输入继续使用原有保守检查，shell 变更检测和 Task/Workdir 范围检查不变。

Plan 审阅器按三个绑定端口的路径和摘要确定加载身份，图轮询创建的新数组不再触发清空与重载。切换项目、Plan，或绑定端口变化仍取消旧请求并重新读取；API 的绑定与摘要校验保持有效。真实浏览器12秒观测从12次附件请求降为3次，另用明确标记的图响应夹具验证绑定变化触发重新读取。

等待人工Plan审批是合法等待态。Run Manager 的失败收尾升级和 goal idle rescan 共用只读审批事件归约：同一Run的 `plan.approval.requested` 在对应 `plan.approved` / `plan.rejected` 到来前，不因Worker静默而触发上述动作。其他Run的审批、其他Plan的裁决不影响当前等待；匹配裁决后恢复原有诊断策略，不修改审批、任务或Run真值。此修复避免长时间审批等待被历史失败候选升级成Run blocked。

保留Worker的源码修复重载器从 `process-guard.v2` 读取 `owner_pid`，同时兼容旧 `pid`，继续检查目标进程身份。此前只读旧字段，导致找不到现有watcher。真实运行中通过此路径更换watcher后，FOUNDATION的Provider会话、派发ID和全部pane PID均未变化；该操作不替代Plan批准，也不重建任务图。

图与Workflow详情复用Run admission归约后的active状态。受控 `run.goal.updated(status=active)` 恢复原blocked Run后，当前Run显示running，终点回到pending，旧blocked事件仍可在历史/证据中查看；历史回放保持当时的blocked，completed/cancelled等不可逆终态不被该恢复事件清除。图不把Worker自报terminal当作Kernel终态。

排队的writer派发后，fanout投影必须保存实际派发的合同authority和execution binding五字段，后续重派覆盖旧值；结果身份绑定不能被排队时旧payload回写为过期authority。字段来自正式事件，复用已有投影metadata模块，不改变合同准入规则。相同来源事件、相同绑定摘要的impl self-check在恢复重放时只记一次完成事件；仍先核验完整self-check正文与合同。真实DRIVE/TRACK原报告无需改写，重建可再生manifest并重载watcher后已被内核自动接纳。

候选质量执行器保留每条Task Contract命令规范化后的`timeout_seconds`，实际执行、超时诊断及命令收据使用同一值；过去统一固定为300秒，导致短命令无法按90秒约束、完整交付的1200秒期限也被截短。旧zf.yaml quality gate命令仍使用300秒默认。运行时只执行已声明的机械期限，超时保持失败，不替代命令自身的环境准备或语义验收。

保留Provider的watcher重载后，Codex观察器从同一instance最近的`worker.launch_artifact.written`恢复会话发现时间边界。此前内存_spawn_ts丢失后回退到0，可能在新的延迟创建rollout出现前选中旧文件，重放历史文本并漏掉当前会话。恢复仍读取role-local目录，并保留当前新rollout的完整首轮重放以捕获快速task_complete；其他角色的启动时间不影响本角色。

受控候选返工与Kernel恢复扫描共用带状态目录文件锁的Plan触发追加边界。即使两方从旧事件快照各自构造同一触发，也在追加前重新读取账本，并按Flow触发类型、Run、PDD、失败来源和返工次数复用已有事件；不同Run、失败来源或新返工次数仍可派发。事件与历史取消记录保留，不直接修改Planner状态或绕过Plan审批。

执行准备skill补充冷writer、候选集成器与独立Verify分别审查的要求，包括真实命令顺序、镜像与锁定版本、依赖重装后的缓存和嵌套期限。此类技术栈与环境方案仍由Planner/Critic及项目runner负责，Kernel不猜测安装命令。

已接纳的Worker失败/blocked结果将对应TaskAttempt记为不可重试的semantic_result_failed后，同步关闭仍在running且Run、Task及dispatch完全匹配的Operation。恢复扫描也可从canonical TaskAttempt与真实结果事件补记遗漏的Operation失败；已终结或新dispatch不受旧失败影响。使用WorkflowOperationService写Kernel事件，图直接读取真实终态，不自行改显示状态。

Discovery缺口替换已有任务时，Task Map amendment除重连依赖外，也将所有显式modules[].task_ids中的被替换ID映射到声明的后继；一对多继承全部后继，其他模块元数据保持，原Plan对象不被修改。不存在的引用仍由原校验拒绝，不根据目录猜测模块归属。

候选级重规划已通过`orchestrator.replan_requested`及对应Plan fanout正式开始时，同Run、PDD和旧Task Map generation的下游reader失败不再竞争通用阶段重试或触发cap终止整个Run。绑定使用真实trigger的代际及返工来源/次数，不能仅凭存在其他活跃Agent抑制失败。只覆盖明确的候选返工范围；其他Run/代际/任务、未派发或已终止的重规划，以及新Planner自身失败，仍执行原有重试上限。失败原事件保留，Plan审批及最终验收不因此通过。

受控候选重规划将既有语义上下文字段同时传入持久marker与即时resynth，恢复扫描沿用同一字段集。显式指定的`previous_plan_candidate_refs`覆盖历史触发中的旧引用，required_actions及授权来源保持，不因即时路径丢字段而与恢复路径产生不同输入。下游仍校验引用摘要和读取契约。Reader的未启动触发恢复将`rework_attempt`计入身份：同一来源的新重试可在Run恢复后补派，已经派发的同轮重复触发不再启动。

Plan审批去重按Run、阶段、产品、绑定的Plan/合同版本及完整任务内容生成带版本指纹，不再仅比较Task ID集合。相同候选的重复请求仍被抑制；新内容或不同Run可以进入独立预览。旧版任务ID指纹不足以证明候选相同，升级后不据此阻断新版。恢复时重新评估当前重复关系，历史plan.minting.suppressed只保留审计事实，不永久封锁Plan，也不产生自动批准。

`zf start`按EventWatcher读到的不可变快照批量调用一次调度循环。快照内的观察与唤醒事件保持账本顺序，处理后只提交该快照的字节游标；处理期间新追加的事件由下一轮消费。每种唤醒的过滤和限流仍逐项检查，纯观察或全部限流的快照仍交给周期性durable catch-up。模拟终态强制进入调度，在Task收敛后才追加`simulation.done`并停watcher。此变更减少密集唤醒引起的重复调度扫描，不改变Worker报告的证据准入。

Codex会话被`RoleSessionRegistry.clear()`明确废弃时，在同一文件锁内持久记录退休UUID；迟到Hook或观察器不能把它重新绑定为当前会话，也不能覆写新的Task/Stage绑定。观察器跳过已退休rollout，即使其mtime因迟到写入而更新，并检查最终绑定结果以覆盖扫描与回收的竞态。Hook仍按原角色写入审计账本，新UUID正常接纳，Claude会话策略不变。此边界防止已完成reader回收后，旧上下文用量再次阻塞下一条排队lane。

Reader fanout的`worker.context.critical`只由其fanout回收/重试生命周期恢复，不再触发普通产品Task的completion audit重新排队。归属同时核对reader topology及原Kernel派发的fanout/stage/child/run、角色、Task和trace；仅有相同Task或角色不够。升级前已排出的context retry也回溯原trigger并应用同一边界，旧attempt结束后仍不能重排writer。普通Task及writer fanout的现有context审计不变。

YAML完整结构中的role节点也关联真实Operation：有running操作时保留运行态，不被预注册的排队子任务覆盖；否则展示最新调用状态。aggregate节点按本阶段最新fanout的Kernel汇总事件变化，新的fanout先回到pending，旧fanout迟到完成及Worker自报不覆盖当前状态。结构节点记录Operation或来源事件引用，与阶段概览共用既有只读账本投影，不产生完成事件。

已发生的编译触发事件节点显示reported并关联source_event_ids；该状态仅证明事件发生，不构成成功或交付裁决。

CallResult协议修正可保留不可变CallRequest的初始attempt身份；TaskAttempt收尾以Kernel已接纳的fanout.child.completed为执行关联，核对来源结果、Task、Run和Operation后精确匹配实际dispatch。迟到旧结果不能关闭新重试；shadow租约过期可按既有恢复入口补记成功，保留旧失败事件及诊断历史。

当前generation的Kernel run.goal.completed也为其pdd_id/feature_id顶层需求任务提供接受证据，和运行时对需求容器的收尾一致；仍要求Run、generation与Kernel来源匹配，不凭TaskStore done字段涂绿。
