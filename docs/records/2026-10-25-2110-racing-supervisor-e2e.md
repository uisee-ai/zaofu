# 赛车小游戏：187 分支监工端到端验证

> 状态: active
> 执行依据：[完整监工 Prompt](../runbooks/2026-10-25-2110-racing-supervisor-e2e-prompt.md)。基线 `8367e6c`，使用真实 Provider 与 Docker Playwright，进行中的步骤不记为通过。

## 本次环境

- Worktree：`/home/uisee/workspace/zaofu-wt-187-ai-sdlc-graph`，分支 `wip/codex-20261025-ai-sdlc-graph`。
- Dashboard：`0.0.0.0:8002`；dev 的 8001 保持运行。
- 隔离根：`/tmp/zf-racing-supervision-20261025T211020Z`。
- 浏览器新建项目：`racing-project-33598481`，名称 `187 Racing Supervision`。
- 项目配置生成的 state_dir 为 `.zf-187 Racing Supervision`，按配置读取，保留空格路径的真实覆盖。
- Docker：`mcp/playwright:latest`，host network；服务继承 Docker 组，tmux 使用本次独立 `TMUX_TMPDIR`，不复用未加载 Docker 组的旧 server。
- Plan 审批由用户决定；在具体 Plan 交付用户前不请求空泛批准，也不自行批准实现。

## 当前事实

1. 首次空 workspace 展示 onboarding；浏览器先跳过全局引导，再走实际 Add Project 表单创建项目。初次脚本的按钮等待失败属于脚本入口假设，未创建重复项目。
2. Kanban 真实 Codex 问答提出三项选择，已选择 2D 俯视、90 秒积分赛、桌面 Chromium。Agent 识别到当前 PRD 为 2 个实现/验证 lane，未假称 3 lanes 已生效。
3. 生成了待确认 Create task 提案，`prepare_workflow=true`，保留完整 YAML 与 Plan 两道审批。首次浏览器脚本使用全页最后一个 `Create task` 按钮，误点了旧 Pending Proposal，创建 `TASK-5F44B4` 的旧合同；这属于测试脚本定位错误，不能宣称修订稿已建单。
4. 通过 token-gated API 受控恢复：取消旧 Synthesis operation，把真实 Agent 修订稿合同应用到同一个 Task，再准备新请求 `prepare-TASK-5F44B4-c32763f31115843f`。未直接修改 canonical state，未启动 Run。三个动作结果保存在隔离 evidence 的 `recovery-*.json`。
5. 修复准备队列发现后，尚未访问 Workflows 页面时已出现 `workflow.operation.started`（`2026-10-25T21:31:34.235860+00:00`），证明后台接管真实请求；随后浏览器进入 Workflows 观察。
6. Workflow Synthesis 的真实 AskUserQuestion 提出 lane 配置、预算和目标路径三问。浏览器填写项目 root，说明候选编译与已运行配置的不同阶段，并如实保留预算未配置风险；答案入 revision 3，随后生成 PrdFlow Proposal。
7. 初始候选生成 3 Impl / 3 Verify lane，但 Plan 审批为 false。真实 Preview Chat 分别修改审批为 true、PrdFlow model 为 `gpt-6-astra`、impl skills 增加 `zf-plan-task-map-contract`；revision 4/5/6 均有单字段 diff。原候选字节未变。浏览器展开完整 YAML，点击 Apply config，实际 `zf.yaml` 编译后角色数、模型、skills 和审批开关一致，配置摘要 `2b356f6a13021b0616878f71079c83a31abd25d1f262a1fb73ae206778b6a6f3`。
8. 浏览器批准 Workflow 以运行 Scan/Plan：`workflow.invoke.requested` 于 21:39:32 UTC 写入，runtime 于 21:40:30 接纳并就绪，两个 Scan 子角色于 21:40:59 / 21:41:02 派发。Plan 的人工实现审批尚未发生。
9. 产品/技术 Scan 已完成，技术 Scan 从实际 Worker 环境验证 Docker、指定镜像与 host networking；Planner 于 21:47:27 派发。Docker Playwright 对当前真实 Run 的六视角、完整结构、运行中 Planner 的 Provider/模型/最近活动、手机布局通过；旧取消准备显示 History 且无伪 Run ID。图事件序号从 958 增至 1038，证明当前运行持续投影。
10. Planner 首轮产出 4 任务/3 波次、15 项 AC 和 12 条命令，但 22:04:10 的机械准入拒绝 Docker 路径误报和 assembly owner 冲突；内核自动 protocol repair，未计 semantic rework，未进入实现。Planner 自主把骨架移到 lane 0，将 lane 2 留给装配，并明确声明 Docker runner 的生产任务/命令。当前模式为任务片内自检、全部候选汇聚后独立 Verify；不能把 Scan 的双 lane 重叠当作 Impl/Verify 并行证明。
11. 修订版在收拢 FOUNDATION 专用 runner 目录以满足现有 scope 上限后，运行时预校验通过。22:14:17 派发独立 Critic；22:21:23 仍因两项语义缺口退回：DRIVE/TRACK 的中间 demo 缺少本片浏览器命令，防刷分用例把 highWater=10 的前态与首次新增10米的预期混淆。旧机械问题已由 Critic 核对关闭，内核于 22:21:27 自动派发下一轮 Planner。监工未修改裁决、规格或 Plan 批准状态。浏览器对待审批按钮的10分钟等待在此期间超时，属于尚无合格 Preview 的等待期限，不计作 Preview 功能失败。
12. 第三次 Planner 提交补齐 DRIVE/TRACK 的本片 Docker smoke，防刷分拆成八个独立子场景，保持4任务/15AC/3波依赖，命令增至14条。提交前校验0错误，22:35:46 派发第二轮 Critic。Planner 同时发现隔离 Git 工作树内旧 `zf.yaml` 为2 lanes，但本 Run 根项目受控配置为3 lanes；使用匹配 briefing 摘要的有效配置重验，不改控制面或把旧 checkout 配置当作当前 Run 真值。
13. 第二轮 Critic 上下文达到0.9065，超过配置 hard cap 0.9 持续180秒，22:41:27 自动回收。运行时注入同一 fanout 的恢复 briefing，随后按新 session 重派 synth，`semantic_attempt_consumed=false`；此过程没有监工重启或人工修改状态。继续观察恢复后的独立裁决，不将进程存活或重新派发记为审查通过。
14. Critic 正式接纳后，`task_map.ready=evt-a6b18c1e2f25` 于22:50:46产生，`plan.approval.requested=evt-aa21710a4b7b` 于22:50:50产生。此时图接口实际500：绑定 source index 省略可选 authority_refs，被投影当作可迭代对象。先复现5失败/1通过，加入形状判断后89项 Plan/Inbox/图/Chat影响闭包通过。仅重载8002 Web，runtime保持等待审批；Docker浏览器随后读到完整25125字符正文和15个绑定附件，Plan为pending。未手改source index或审批事件。
15. 实际 Plan Chat 选中 PRD 摘要段，仅追加结束面板按钮“重新比赛”的文案要求。Agent diff 与预期逐字一致，引用外正文不变；待确认草稿时批准按钮被禁用。浏览器确认局部修改产生 `plan.rejected=evt-d1196c40945f`，审阅工件摘要 `baf19be49ce30312d1ee0576eb8e9b76093b2a4346fd4b4b7010794d2f68f359`；旧正文摘要 `c7bba1969a075e626bb6daab5a0552abcb65f8857b7b31caa740fb09b5accdea` 仍一致。22:54:52 内核派发重新规划，未产生 plan.approved 或 Impl 派发。新版本是否完整接纳仍待验证。
16. 并行的 `orchestrator` 角色只执行 `checkpoint_policy=shadow` 的语义审查，不是第二状态机。它建议补齐 `VC-ASSEMBLY` 的具名本片验收映射及 TDD 断言：机械 self-check 仍要求真实命令收据，但本片命令的 acceptance_ids 为空。监工将此作为计划质量缺口，在文案修订完成后通过同一受控 Plan Chat 修订，不降低运行时门禁，也不把 shadow 建议当作已生效的 canonical 返工。
17. 文案重规划时，`apply_patch` 仅写 artifacts/plan/task_map.json，却因正文提及账本路径被 runtime write guard 拒绝。监工复现 apply_patch/Write/Edit 三种合法引用误报，真实 protected target 的增改删移和未知输入五项保持拒绝；复用现有目标解析器后64项 Hook/调用链/集成测试通过。Planner 自主使用审阅工件和事件ID保留来源，原拒绝未被删除；没有代替 Worker 写产物或伪造 Hook 成功。
18. 文案新版本 `evt-99bf2fe56f16` 于23:11:35接纳，23:11:38再次请求审批。Critic核对全文与确认候选一致，文案同步到PRD、AC-A02、浏览器场景及产品验收规格，要求真实点击后完整重置。此时shadow审查仍指出assembly自检映射缺口；没有把Critic批准当作用户Plan批准。
19. 第二次真实Plan Chat只在选中自检段落追加具名验收/TDD映射要求，保留完整90秒和候选集成后独立验证责任。首个脚本断言错误地期待一个未发送给Agent的空格，未应用候选；修正脚本后逐字局部diff、引用外不变及审批按钮禁用均通过。确认产生 `plan.rejected=evt-2992693c229e`（23:17:04），不可变审阅摘要 `67096ba35da5c33ff46de80373d732bff4ec9e31896fd62d5d18179e9fe03a2a`，23:17:24开始重新规划，尚未批准实现。
20. 真实页面12秒观测到同版三个Plan附件共请求12次，加载提示反复出现。根因是图轮询每次创建新的artifacts数组，触发审阅器Effect清空并重载。按端口路径/摘要稳定依赖后，同样真实观测只有3次请求、12次采样均持续显示内容。独立Docker浏览器回归用明确的图响应夹具证明同版轮询不重载、绑定变化后重新读取，1 passed；构建、类型及bundle预算通过。
21. 第二次Plan Chat在23:17:24进入重规划，Planner正式预检零错误，`prd.plan.child.completed=evt-e501a93cea9a`。独立Critic核对新增AC-A07的五组场景、验收矩阵与运行时责任选择一致，23:42:45生成 `task_map.ready=evt-adf3807d914c`，23:42:47生成 `plan.approval.requested=evt-40e85f0b4734`。原4任务、依赖、lane归属及15项AC保留，新增后共16项；3个Impl/Verify lane已由实际配置编译核对，依赖图最大实现并行宽度为2。没有Plan批准或Impl派发。
22. 真实Docker浏览器读到新版25377字符正文，与绑定SHA-256 `c12ce3ef9dd16ba97c60259ba3ee75dca12d19d8397f67f5127f6546f9d2cc28` 一致；在模块/验收场景中找到AC-A07，审批按钮可用但未点击，桌面和手机没有横向溢出。最终两张Viewport截图已目视核对有效。完整PRD、SDD接口和Plan已导出，[审批摘要](187-racing-supervisor-e2e/approval-review.md)交付用户，并请求批准这一具体版本。此确认来自用户明确要求，不是监工自行追加的流程。
23. 用户明确回复“批准，继续”后，浏览器再次核对同一Plan及正文摘要，点击“确认计划并继续”，2026-10-26 00:01:28产生唯一 `plan.approved=evt-158a8630051c`。点击后脚本误取请求顶层plan_id，真实字段在payload内，导致测试断言失败；没有重复提交，随后从受控动作完成收据和正式审批事件补齐审计。午夜EventLog已归档，监工临时读取脚本改用项目配置下的EventLog.read_all，未将当日活跃文件误当完整账本；产品图接口本身始终正确读取历史。
24. 审批后没有Impl派发，发现23:55:13的 `run.goal.blocked=evt-de04e59f0b5a`：Run Manager在人工等待超过300秒后，将22:21的历史failure-closeout候选升级成人工收尾并终止Run。23:50还发生了不必要的goal idle rescan。原有防护覆盖运行中Operation及近期Worker活动，遗漏了同Run待审批Plan。新增反例先复现6失败/23通过；共用只读审批归约后234项影响闭包、51项sentinel、10项flow-smoke通过，另以真实3300条事件前缀回放，20次idle探测无错误升级或重扫。
25. 确认没有运行中Operation后，用本项目 `zf stop --include-run-manager` 平稳停机，保留workdirs；受控 `human-decision-dismiss` 关闭误升级项 `hdec-5cd556415d99`（`evt-d68bf23ae39c`），没有激活失败backlog。`zf goal set --status active` 产生 `evt-0e5dacd557be`，恢复同Run和根Task，再启动加载修复后的runtime。00:09:01内核自动派发 `prd-dev-lane-0-RACE-FOUNDATION`（`evt-ebd2fe29a7a8`），实际Worker随后开始读取briefing。未重写账本或canonical state，未新建Plan/Run，也未重复批准。

## 发现与处理

| 问题 | 证据 / 处理 | 状态 |
| --- | --- | --- |
| `.gitignore` 被当作 scope 说明文字丢弃，Channel 同一路径被拒绝 | 两条实际消费链回归先失败；共享路径形状判断保留 dotfile，canonical 路径安全校验不变；69 passed、47 deselected | 真实 Agent 新提案保留 dotfile，受控更新后的 Task 合同一致 |
| Task 草稿把 Chromium 安装安排在宿主 | 通过真实 Kanban Chat 补充 Docker-only 约束，改为将来交付的 `scripts/verify-racing.sh`，保留正常 90 秒浏览器验收 | 修订稿及受控 Task 更新已核对 |
| 新注册项目准备队列必须访问 Workflows 才消费 | 原 Worker 仅 watch 启动默认项目及 Workflow 路由读到的项目；新增启动后注册项目的实际 create-task API 回归，不调用 Workflow GET，先复现 ready 挂起；Worker 每轮发现 workspace 注册项目，保持并发上限和进程租约 | 45 项准备/合成调用链测试通过；真实请求自动消费已观察 |
| 多角色启动超过 30 秒被误报 STOP | 真实后台继续启动并接纳同一 invoke，但旧 API 写 runtime.start.failed 并返回 503；慢启动回归先失败，修复将活进程尚未就绪保留为 starting/202，真实退出仍失败 | 47 项启动与 CLI/Web/受控调用链测试通过，另 3 项 host tier 未在该命令运行 |
| 取消准备请求仍显示待决策，Synthesis ID 冒充 Run ID | 真实取消请求在列表中仍 ready；回归先失败。详情只从 Run admission/已绑定 Run 获取运行身份，列表与详情仅对当前 revision 的取消准备投影 cancelled，旧代取消不污染新修订 | 58 项 Web/图/准备调用链通过，前端 build/typecheck/unit/bundle budget 通过 |
| Docker `--pull never` 导致验证输入路径误报 | 提取器将策略值 `never` 当作镜像，后续挂载和镜像被错误当作仓库路径；新增两种 pull 语法回归先得到 1 failed / 2 passed，加入值参数识别，不修改路径门禁 | Task Map / evidence / Plan preflight / counterexamples 100 passed；真实 Planner 同时自主改为显式 Docker runner，未据此宣称运行时热加载 |
| 合格 Plan 已请求审批但 Workflow 图500 | source index 的可选 authority_refs 缺失导致 NoneType 遍历异常；回归覆盖缺失/null/错误形状及真实图路由，原正文、pending和只读事件计数保持 | 89项影响闭包通过；重载8002后真实浏览器成功读取Plan正文和附件 |
| 规划文档引用账本路径被误判为直接写账本 | 原保护扫描完整工具输入，混淆正文与文件目标；复用已有写入目标解析器，未知输入及shell检查不变；3失败/5通过的反例先复现 | 64项Hook、调用链和Codex路由集成测试通过，真实账本增改删移仍拒绝 |
| 图轮询使Plan审阅内容周期性消失并重复加载 | 真实浏览器先复现12秒12次请求；加载依赖改为绑定路径/摘要，项目及Plan切换仍取消旧请求 | 同样真实浏览器3次请求且持续可读；绑定变更浏览器回归1 passed，build/typecheck/bundle通过 |
| 人工Plan审批等待被当作停滞，历史失败收尾将Run误终止 | 同Run待决审批在匹配裁决前阻止失败收尾升级及goal idle rescan；其他Run、错误Plan裁决、已处理Plan保持原有行为 | 234项影响闭包、51项sentinel、10项Mock通过；真实事件前缀回放通过，受控恢复后同Plan已自动进入Impl |

26. 为供应已批准Plan要求的 `RACING_WORKFLOW_EVIDENCE`，监工从只读账本与绑定工件生成前置治理证据，保留人工批准、配置/YAML预览/启动授权及已发生writer派发的原始引用与摘要，不填未来Verify/Judge结果。证据作为外部环境输入，不修改受控zf.yaml。准备保留Worker重载时发现actuator只读旧pid字段，真实锁为process-guard.v2/owner_pid；回归先失败，修复后212项actuator/process guard/CLI start/Run Manager测试通过，sentinel 51通过。真实重载使watcher PID从1559070变为1602023，FOUNDATION会话与派发不变，全部Provider pane PID不变。读取sg子进程的/proc/environ被宿主权限拒绝，环境消费仍须以后续真实audit结果证明，不将wrapper赋值当作已通过治理验收。

27. 重载后真实Docker浏览器核对Plan为approved，运行中的FOUNDATION节点绑定实际Provider用量：`prd-dev-lane-0 / codex / gpt-6-astra`，详情显示真实工具活动。首轮模块与执行截图过早捕获加载态，不作证据；补充等待canvas节点后重新截图并目视核对。截图同时发现Run节点仍显示恢复前blocked，后续继续修复该投影不一致。仅对确认idle的装配与三个Verify角色执行正式 `zf restart <role>` 以加载外部证据路径，没有替换活跃FOUNDATION。此时Impl观察峰值仍为1，不声称已完成多lane验收。

本轮恢复修复验证：审批等待影响闭包234 passed；重载器及调用链212 passed；最新sentinel 51 passed；`dev-verify plan/run --base dev`审阅整条分支的broad建议，按已完成影响闭包跳过deterministic/serial及未变化且已验证的Web/contract，实际运行flow-smoke 10 passed（6.63秒）。一次手写命令误用了不存在的 `tests/test_flow_smoke.py`，无用例执行，错误日志保留；随后使用正式脚本通过。没有运行全量pytest。

本记录后续补入实际并行区间、门禁与最终交付。原始认证 token、storageState 和带认证 trace 不进入仓库。

28. FOUNDATION于00:29:37产生真实 `dev.build.done=evt-be0dfb11bd11`；内核随后分别在00:29:42、00:29:46向lane 1/2派发DRIVE/TRACK（`evt-a081f912774a`、`evt-858a6a8aa9d9`）。运行时按可用slot分配实际role，Plan里的lane归属不是静态线程绑定。两个真实Provider均开始实现，Impl峰值2；3个配置lane不意味着依赖宽度3。`parallel-intervals.json`按正式派发/settlement计算，未关闭区间只统计到观察时刻，Verify仍未发生。
29. 当前Run原有admission投影为running，但图及详情仍使用旧 `run.goal.blocked`。新增真实API+历史回放反例1失败/3通过；复用既有admission状态后115项图、Web Proposal、Run admission、请求验收与Goal coverage测试通过，sentinel51通过。仅重启8002 Web，实际Docker浏览器验证Run=running、详情无旧terminal、终点pending、两个Impl operation同时running；历史blocked证据仍保留。此前等待/重载修复提交为 `e5c7f64 fix: preserve manual plan waits and worker-safe reloads`。

已导出审阅用问答、候选YAML、实际配置、局部修订回执、人工批准与Impl首次派发和验证日志，见 [证据清单与SHA-256](187-racing-supervisor-e2e/manifest.json)。审批文档保留批准前快照；当前状态以本记录及正式事件为准，不包含尚未发生的Verify或产品完成证明。

修复提交：`bd11edf fix: harden workflow preparation and plan review gates`。最终自动验证器再次执行Web类型检查和全部模型测试通过；已单独通过的后端影响闭包、sentinel及Mock E2E不重复运行。尚未merge/push。工作区保留本次运行需要的`.venv`和`web/node_modules`临时符号链接，8002与runtime留供用户审批，任务未归档。

补充真实 host + Mock Provider 层：`tests/e2e/test_workflow_runtime_autostart.py -k starting -m host` 的 CLI / Kanban / Channel 三入口全部通过（49.74 秒）。将同步等待设为 0，使回执实际返回 starting；各入口随后自动启动 watcher、接纳 invoke 并派发，重复批准不生成新 invoke，没有 runtime.start.failed。每个测试使用独立临时 tmux socket，并清理自己的 runtime。这一层不冒充真实 Provider 验证。

本轮 `dev-verify plan/run --base dev` 覆盖整条 187 分支，文件阈值扩为 broad deterministic。按 AGENTS 的域与影响闭包执行上述 focused 验证，自动运行器跳过已执行的 Web/contract 及 broad deterministic/serial，补跑 flow-smoke：10 passed（4.68 秒）。没有运行或声称通过全量 pytest。

后续Hook与Preview修复的最终sentinel为51 passed（8.49秒），flow-smoke为10 passed（15.47秒）。首次sentinel因hook_recv.py超过1000行而失败；将目标文本归一化放到已有hook_workdir_guard责任模块后997行，不提高结构豁免上限。

与Prompt的差异：3个Impl/Verify lanes由真实Synthesis问答进入候选，而非另做一次lane数Chat修改；Workflow Chat实际验证审批开关、model和skills三项。两张真实Plan局部diff截图捕获位置失效，不作视觉证据，保留真实API与DOM断言；另以完整Plan候选的明确UI夹具复测桌面/手机diff可读且不横向溢出，1 passed，不冒充真实Provider执行证据。Docker镜像内没有@playwright/test runner，浏览器回归将已有前端node_modules只读挂入容器，仍使用容器Chromium，未安装宿主浏览器。

恢复投影的验证器按本次5个改动路径（仍以dev为base）规划80个关联测试文件；已执行明确的115项影响闭包及51项sentinel，匹配run跳过已覆盖deterministic与未改变的Web层，没有额外执行全量或把跳过的tier计为通过。真实浏览器当前与历史状态由上述API回归及Docker复核共同验证。
