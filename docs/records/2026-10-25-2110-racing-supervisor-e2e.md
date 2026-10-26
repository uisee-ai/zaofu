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

30. DRIVE提交`2d58b91886ecdb7e92c3776c7ba7a74e85313ea2`、TRACK提交`fc3f13f`，分别于00:52:49/00:55:32产生`dev.build.done`。两者真实Docker浏览器与本片测试完成，但结果被运行时标为stale_contract_authority：dispatch及Worker self-check使用当前authority，fanout投影遗漏五个authority/execution binding字段，结果binder错误采用排队旧payload。同时恢复循环反复写相同impl.self_check.completed。反例先复现3失败/1通过，修复保存正式dispatch元数据并对同来源/摘要去重，未放宽合同验证。两项旧兼容性测试需要从模拟“untyped replacement”的原始事件与投影中同时去除新增字段，保持原有成功/拒绝断言。
31. 使用原5517条事件经`FanoutManifestProjector.write_manifest`重建可再生manifest，核对账本未变、未写TaskStore；保留Provider会话重载watcher。01:05:14/15内核自动接纳DRIVE/TRACK原结果（`evt-049f56c81c3a`、`evt-01ade5921a6d`），01:05:18派发ASSEMBLY（`evt-64b8d0dcad37`，实际lane 2）。监工没有代写产品代码、补造Worker成功或跳过独立Verify。治理外部证据随第四个实际派发更新，仍不包含未来验证结果。

Impl的dispatch-to-settlement重叠包含等待上述修复的时间，不能把全部区间称为活跃编码；实际两Provider均在00:29至00:53期间工作。配置3 lanes，当前依赖图实现最大宽度2，装配须等待两项交付接纳，独立Verify仍待候选生成。

排队authority修复最终验证：8个调用链/合同模块233 passed（198.78秒），旧合同交接Mock E2E 1 passed（9.80秒），sentinel 51 passed，flow-smoke 10 passed。首次sentinel因fanout.py新增后超过1000行及orchestrator_fanout.py超过既有上限失败；将既有metadata函数移入同责任模块，并撤掉无必要的orchestrator改动后通过，未提高结构豁免。自动验证器规划211个关联文件而扩成broad；实际按AGENTS执行上述影响闭包，run跳过deterministic/serial与已通过contract，仅补跑flow-smoke 10 passed（6.55秒），不声称全量pytest。修复后截至01:14未再生成两份交付的重复self-check事件；修复前544条历史重复事件保留不删除。

32. ASSEMBLY最终交付提交`0864db0d743dacd660685eb0c516299b14124499`，在该提交上`VC-ASSEMBLY`33/33通过，正式90秒旅程仍待独立验收。`dev.build.done=evt-45c3926d0db5`于01:31:02产生，01:31:07内核接纳`evt-3e017c1b34af`，4个原实现子任务全部交接完成。图在独立验收前仍将全部Task显示in_progress，accepted_event_ids为空，没有把Worker完成自报投影成产品通过。排队authority修复已提交`93b292c fix: preserve dispatched writer authority during replay`。
33. 初轮集成候选`dfe994958e811acbfd95101dafd4a73c4df56eb5`于01:44:25质量失败（`evt-b787a6c135c0`）；14项命令中8项通过、6项失败。全新候选未声明setup_script，前两个命令先于VC-INSTALL，分别缺Vitest/Vite；随后各浏览器命令重新下载约167 MiB的Chromium v1208，180秒内未完成。既有Docker镜像不等于当前项目Playwright包所需版本已经安装；不能把环境失败当行为RED。真实VC-GOVERNANCE已通过，证明读取了监工前置证据；完整90秒比赛尚未执行通过。候选检查目前为顺序执行，只有candidate.ready才启动多lane独立Verify，故此时Verify未运行不是3-lane配置失效。
34. 同轮发现候选执行器丢弃合同timeout_seconds，统一使用300秒。4个确定性反例失败后修复传递规范化期限，并写入实际收据；90秒、1200秒、超时失败及旧300秒fallback均覆盖。19项聚焦、149项候选/返工/验证合同闭包、17项集成调用方及旧交接Mock、51项sentinel、10项flow-smoke通过。首次闭包命令误写不存在的tests/test_verification_commands.py，未执行用例，已改为实际模块重跑；不将该空运行记作通过。自动验证器因179个关联文件建议broad，按AGENTS选择上述明确影响闭包，匹配run只登记已通过tier，不声称全量pytest。
35. 失败后Kernel `workflow_resume_batch`生成同版返工触发`evt-746215246543`并再次请求Plan审批。只读核对10个绑定字段及真实文件摘要，原Plan与返工引用完全相同package `2e0516620b7cb212871cc5a929ef37fc446ac1c6b834296c85c5db1e77588f4a`、Task Map generation、需求及Run contract，没有新产品范围。监工依据用户对原版的明确批准及持续修复授权，经token-gated受控入口记录`plan.approved=evt-c94ace449772`，reason引用原`evt-158a8630051c`并明确不是用户再次操作。未关闭Plan审批开关、未为新语义计划设自动豁免。01:51:38/40/43重新派发FOUNDATION、DRIVE、TRACK。
36. 通过保留Worker的正式actuator重载watcher（新PID1845927）以加载合同超时修复。DRIVE会话恢复时出现旧文本集中重放，不能将这些新账本时间戳当作新的编码活动；后续并行证据按本轮派发、实际工具活动和交付结果核对。此现象继续调查，未删除历史事件或伪造执行状态。

37. 历史文本重放已定位为观察器恢复缺陷：DRIVE在01:05清理旧会话并启动新进程，但首次输入到01:51才写新rollout；watcher重载丢失内存_spawn_ts，后台观察以0作为时间边界，抢先选中旧rollout。稍后Hook已绑定正确新会话，tailer仍留在旧文件，因此重放旧文本且遗漏新文本。确定性竞态反例先失败，改为从同instance正式launch事件恢复边界后48项聚焦、198项启动/注册/生命周期/CLI闭包、51项sentinel和10项flow-smoke通过。真实只读回放确认旧mtime早于恢复边界、新文件晚于边界且选择匹配Hook绑定；经正式actuator保留Provider重载watcher。
38. 返工FOUNDATION发现宿主代理未进入Docker，增加仅用于下载的代理白名单与0600临时文件回归，检查点`1060cc46a88c51593d213bef3dbcf0fbfb406084`；传递后下载仍在180秒超时，未把环境探针成功算作产品通过。TRACK检查点`ddc5aed`、DRIVE检查点`82dd2884ee23d76d6e6cf7a025a3e8227950e3ec`保留通过的本片单测与真实浏览器阻塞日志，各自遵守共享runner范围并报告blocked。内核将子任务标为failed，继续派发ASSEMBLY及独立Discovery做归因；Verify尚未运行。

观察器修复的dev-verify规划189个关联文件而建议broad；按AGENTS采用已完成198项明确闭包，匹配run登记跳过deterministic/serial，不宣称全量通过。

39. 独立监工探针在被拒绝的原候选`dfe994`隔离checkout上显式安装依赖并构建，使用Docker镜像自带Chromium 149，不修改正式Run裁决。真实按键、暂停冻结、重开重置、自然90秒结束（单调时钟90294.5ms）、单次结算、结束后冻结和“重新比赛”全部通过，无console/pageerror。截图28与source文件摘要可核验。该结果只排查游戏行为，不能替代当前候选质量门或独立Verify。探针8023服务已停止，注册的隔离worktree留待最终清理。
40. 受控`candidate-rework-apply/replan`于02:29接纳环境计划修订（`evt-b54797ff6f28`）。反馈明确原Plan安装180秒、依赖准备只覆盖writer、npm ci清除浏览器缓存等缺口；不修改运行中YAML，不降低16项产品AC或90秒要求，不声明新用户审批。反馈通过必读`plan-rework-context` sidecar（SHA3883bdca）交给真实Planner。原第一Planner被重复trigger替代，新`evt-442e54ec`已继续同一修订。
41. 将冷环境消费者、命令顺序、镜像/项目版本和重装后缓存审查补入通用执行准备skill，canonical及两分发副本一致。23项技能物化/指令检查通过，1项已有可选example跳过；本次在途Planner使用派发时的冻结skill，不冒称热升级。Codex观察边界修复已提交`7636473 fix: recover Codex observation boundaries after restart`。

42. 真实环境返工出现两个同源scan触发`evt-79e1f868b6f4`和`evt-442e54ec2fbd`，导致第一Planner刚启动即被superseded并重启。受控动作写marker后构造trigger期间，恢复扫描也可能读取未完成快照；双方原来直接追加。实际受控动作+Kernel消费交错反例先得到6个触发（应3），修复为锁内重新读取账本后复用同一返工事件。另验证4个并发writer只追加1次、新attempt仍允许。138项合同/动作/EventLog/锁闭包、56项Run Manager/Writer调用方（316 deselected）、51项sentinel、4项语义控制Mock及10项flow-smoke通过。首次闭包包含不存在的测试文件，未执行用例；已记录失败命令并按真实模块重跑。

返工竞态的dev-verify规划198个关联文件而扩成broad；按AGENTS采用已运行的具体影响闭包，匹配run登记跳过已完成tier，不声称全量pytest。正式actuator保留Provider重载watcher，同时仅重启8002 Web；8001未动。独立Discovery另复现交付脚本对缺失/无效90秒字段错误放行，正在正式报告，最终闭环尚未完成。

43. 实时图错误保留4个返工writer为running。账本与TaskAttempt Store均已记录失败，但Operation未收尾。新增回归覆盖dev.blocked/dev.failed的即时及恢复路径、旧dispatch不关闭新dispatch，并检查图终态。最初测试将Worker事件经Kernel writer追加时漏设origin，已纠正；在隔离测试进程加载修复前HEAD函数重跑，仍4 failed/1 passed。修复经TaskAttempt规范状态与真实结果来源、Run/Task/dispatch匹配后，通过WorkflowOperationService补记失败，未修改Graph标签。125项Operation/Attempt/图闭包、16项writer调用方（142 deselected）通过。
44. 正式重载watcher后02:53:45补记`evt-6e00a8595a3f`、`evt-b1758406d125`、`evt-322f7ac9b8e9`、`evt-ebc4cd5a7300`。Docker Playwright核验4节点均failed，Run仍running、Task未显示done，详情保留真实Provider、失败来源与最近活动；截图29/30已人工查看。补记时间表示Operation收尾时间，不宣称期间Worker持续编码。
45. Discovery的真实计时字段缺陷已形成`RACE-ASSEMBLY-TIMING-REPAIR`，但两次amend被旧modules引用阻断（`evt-381d4ed7925f`、`evt-4cd709268f39`）。两个反例先复现单/多后继替换失败；修复仅重绑明确supersedes映射。76项gap/Task Map/模块/真实runtime调用方通过；对本次`evt-dede9ac3a65e`原始输入只读回放也完整校验通过，未改旧工件或账本。修复已加载，实际缺口重新接纳尚待验证；不把只读回放算成任务已派发。
46. 两项修复合并验证另有51项sentinel、5项语义控制/旧合同Mock E2E、10项flow-smoke通过。dev-verify按共享边界规划较宽；实际采用上述具体闭包，匹配run登记跳过已通过tier，不声称全量测试。返工触发竞态已提交`7eab802 fix: deduplicate concurrent plan recovery triggers`。

47. 03:02:57旧候选DRIVE Discovery再次环境失败（`evt-c8b21d50a8fc`），通用阶段cap触发`human.escalate=evt-093d49440983`和`run.goal.blocked=evt-2eb794871018`；03:03:07取消正在进行的环境Plan（`evt-52b81862362d`）。Critic继续运行，03:15提交建议通过`fanout.synth.completed=evt-0f1857beaa75`，但取消后的报告不构成正式Plan接纳，没有新版task_map.ready。180秒浏览器等待因此超时，不能计作Preview成功，也不是已证明的Preview渲染故障。
48. 复现旧reader失败抢占活跃候选重规划的恢复所有权：更正测试导入名后2 failed/11 passed。新增保护要求正式返工marker、同Run/PDD/旧代际、匹配返工来源与次数的活跃Plan fanout；其他范围、终止的Plan及新Planner自身失败不豁免。首次真实回放发现fanout.started不在顶层携带代际，已按实际trigger引用绑定并补充反例，而非放宽身份检查。最终117项stage/recovery/escalation/真实调用方测试、51项sentinel、5项Mock E2E、10项flow-smoke通过；8467条真实事件前缀回放从cap_exhausted变为owned_by_active_candidate_replan，未改历史账本。最初只读脚本误用Pydantic API，已改用EventLog原生对象。dev-verify规划201个关联文件而扩成broad；按AGENTS执行上述具体闭包，匹配run登记跳过deterministic/serial，没有运行全量pytest。
49. 正式保留Provider重载watcher，从2001347切到2049658。监工通过`zf goal set`记录operator_review并恢复同Run（`evt-50445443495c`），保留旧cap/cancellation事实；受控候选重规划产生`evt-49490d05913e`和唯一新trigger`evt-bddccfe072a9`。24份带摘要的最近环境Plan/Critic工件作为previous candidate输入，反馈要求复用已完成环境设计，仅把Discovery已证实的时长字段校验缺陷纳入原ASSEMBLY/AC-A07。新Plan尚未批准，正式Impl/Verify及最终产品验收仍待执行。此前两项Operation/module修复已提交`e2cf090 fix: preserve failure and module state during rework`。
