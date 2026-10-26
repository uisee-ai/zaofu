# 187 Workflow 图工作台实施与验证

> 状态：本批实施与真实端到端验收完成。设计入口：[187](../design/187-ai-sdlc-graph-workbench.md)。

## 实现边界

采用 **skills 提供方法、Agent 输出语义关系、代码校验并渲染事实** 的方式。Planner 在现有 Task Map 中声明 `modules`、条款与任务引用；模块不会改变调度，也不会复制 Task。不同项目共享相同 schema、投影和 React Flow 组件，无产品名称、业务目录或任务类型的硬编码布局。

`WorkflowProposalPage` 内提供生命周期、模块与任务、Workflow 执行、追溯与变更、验证与交付、时间线六个视角。结构来自 Proposal 固定的编译配置，动态叠层来自当前 Request/Run 的 EventLog、TaskStore、Workflow Operation、Fanout、Attempt 和已绑定计划工件。未声明模块、缺少证据和未接入生产环境均明确显示，不补造成功节点。

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
2. 在 Workflows 审阅完整 YAML；通过 Chat 选择并行数、模型、角色 skills 等字段提出局部修改，检查 diff，应用后重新审阅新版本。
3. 需要先审阅计划时，在配置预览中打开“计划生成后等待审批”。应用候选配置，再批准运行；runtime 由现有启动链自动拉起。
4. Scan/Plan 生成已绑定文档后，点击 `PRD / Plan Preview`。在 Agent Chat 原文区域选择片段，提出修改并检查候选全文，确认后走既有 Plan reject / replan 决策路径。
5. Planner 重新提交计划后再批准实施。在 Workflow 执行视角观察阶段、Agent、尝试和返工；从模块/任务进入追溯与验证视角核对提交及证据。

局部修改不等于批准。配置修改始终重新编译、生成新 Proposal；文档修改生成不可变 sidecar，原 Plan 不原地覆盖。已处理的计划不能用旧草稿再次修改；执行中的变更必须经既有受控重新规划入口。

## 状态与数据限制

- Task 绿色完成要求当前代内的内核 `task.done.accepted`，或同 Run、同代次 `run.goal.completed` 中明确列出的 `completed_task_ids`，同时 canonical Task 已置 done；Worker 自述仅显示已报告。复用 Goal Coverage 的已接纳结果、目标提交和代次校验，过期验证不计入当前闭合。TaskStore 中的提交链接只提供追溯。
- 历史模式按事件游标读取，无法重建的 Task 历史状态显示未知。需求与配置取已绑定 Proposal，不重建旧准备修订；后续编辑当前 `zf.yaml` 不改写旧 Run 图。
- 下游影响表示已声明关系可达；未声明业务依赖仍待 Agent/人工评估。
- 时间线只使用实际开始/结束/请求时间，缺少时长不估算 ETA；关键路径计算 Task 操作区间并集后的 DAG 最长路径，不累加并行任务。
- 当前未配置部署/生产健康数据源，发布与生产健康保持独立；代码交付不等于生产健康。
- API 返回完整快照与 total/included/truncated 统计；画布使用可见区域渲染和层级折叠，1200 节点夹具验证无遗漏。当前没有服务端邻居分页。
- 每 2 秒拉取一致快照，后台页面暂停轮询；返回实时后重新读取完整快照。请求有并发/范围保护。Live Events 只传事件索引，活动详情中的大型工具输入/输出使用有标记的摘要，完整正文保留在 EventLog / sidecar。

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

Provider 的结构化文件写入保护复用现有目标解析器，只检查实际 Add/Update/Delete/Move 或 Write/Edit 目标，避免把规划文档正文里的账本引用当作写账本。未知输入继续使用原有保守检查，shell 变更检测和 Task/Workdir 范围检查不变。

Plan 审阅器按三个绑定端口的路径和摘要确定加载身份，图轮询创建的新数组不再触发清空与重载。切换项目、Plan，或绑定端口变化仍取消旧请求并重新读取；API 的绑定与摘要校验保持有效。真实浏览器12秒观测从12次附件请求降为3次，另用明确标记的图响应夹具验证绑定变化触发重新读取。

等待人工Plan审批是合法等待态。Run Manager 的失败收尾升级和 goal idle rescan 共用只读审批事件归约：同一Run的 `plan.approval.requested` 在对应 `plan.approved` / `plan.rejected` 到来前，不因Worker静默而触发上述动作。其他Run的审批、其他Plan的裁决不影响当前等待；匹配裁决后恢复原有诊断策略，不修改审批、任务或Run真值。此修复避免长时间审批等待被历史失败候选升级成Run blocked。

保留Worker的源码修复重载器从 `process-guard.v2` 读取 `owner_pid`，同时兼容旧 `pid`，继续检查目标进程身份。此前只读旧字段，导致找不到现有watcher。真实运行中通过此路径更换watcher后，FOUNDATION的Provider会话、派发ID和全部pane PID均未变化；该操作不替代Plan批准，也不重建任务图。

图与Workflow详情复用Run admission归约后的active状态。受控 `run.goal.updated(status=active)` 恢复原blocked Run后，当前Run显示running，终点回到pending，旧blocked事件仍可在历史/证据中查看；历史回放保持当时的blocked，completed/cancelled等不可逆终态不被该恢复事件清除。图不把Worker自报terminal当作Kernel终态。
