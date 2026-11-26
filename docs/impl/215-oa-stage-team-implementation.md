# 215 · OA 阶段组队的实施与验收

开发起点为 `dev@9451996a`。PRD 可在既有 Workflow Synthesis 的 `short_flow_spec.parameters.stage_teams` 中声明本次分工。Proposal 固化被选团队和配置。OA 可改变分工，WRC 根据实际就绪和空闲席位派发。阶段签署、Plan 审批、Judge 判词和交付回执分别由原有入口准入。[规格 215](../design/specs/215-oa-stage-teams-and-execution-workbench.md) · [合成方法](../../skills/zf-workflow-synthesis/SKILL.md)

## 按阶段核验

| 范围 | 实施结果和验收依据 |
|---|---|
| Plan | 沿用未声明时的原模板。显式零专家直接派唯一作者，单人和三人分工生成真实 fanout。汇总者获得每项义务的来源、执行实例、attempt 和结果引用。相同代次重放去重。Plan 准入失败后的后继，只有原扫描能对应同一需求和正式 Planner 时才复用意见。人工修订缺少该绑定或输入已变时，重新派发获准的专家组。撤出可选专家须绑定原批准义务，通过后继审批，并指明接手人。旧 PRD profile 和多 kind 配置一并回归。[Plan 合成及回放](../../tests/test_prd_stage_teams.py) |
| Impl 与 Task Verify | OA 在既有 pre_impl 检查点可为多项任务签署一份覆盖方案，记录条款、取证要求和独立签署席。任务合同变化时仅将该项交还 OA。八项任务、三个空闲 Verifier 的验证检查实际 transport 派发、三个独立 workdir、目标提交和重放次数。[OA 准入、局部纠正](../../tests/test_oa_verification_teams.py) · [真实调度入口的 mock E2E](../../tests/e2e/test_oa_stage_team_dispatch_mock_e2e.py) |
| 同一任务的专项证据 | 可从检查点列出的已注册只读 pattern 选择证据提供者。各自在独立 attempt 读取自己的 Task 合同或 `contract:$.field` 切片，并提交意见。主签署人的必读资料包括获准的专项结果。专项失效或合同中缺少所选字段时，记录对应义务和原因，并将该任务交回 OA 局部纠正。OA 可显式采纳适用的旧意见，runtime 复核原 Run、Task、代次、合同、目标、职责、角色、输入范围和结果身份。[取证、重启与来源校验](../../tests/test_task_verify_specialists.py) |
| Candidate Verify | OA 可从注册席位中选择本轮实际宽度。Plan 提交各 Reader 的责任和验收覆盖，OA 采纳该分工摘要。冻结时复核名单和绑定，Reader 的有效判词分别保留。选择两名 Candidate Reader 时，同一项目的八个 Task Verify 席位仍可用。[宽度与冻结测试](../../tests/test_oa_verification_teams.py) · [七份有效判词和一次局部重试](../../tests/test_candidate_reader_seven_plus_one.py) |
| Judge 与交付 | OA 从获授权的正式 Judge 中选择唯一签署者。必需专项意见和已有 Discovery 各有执行身份。可选咨询由独立只读阶段并行获取，已到且绑定有效的意见可以引用。缺少必需意见会阻断主审签署，可选咨询等待或迟达时仍可签署。阻断测试覆盖冒签和过期来源。Judge 与交付各取自己的回执。[独立 Judge 和意见绑定](../../src/zf/runtime/goal_judge_admissions.py) · [验收](../../tests/test_oa_verification_teams.py) |
| Web 和 CLI | 已批准、尚无业务 Task 或 worker 的 Plan 可见全部应到成员。随后按义务的实际绑定更新；两个协作者或多次尝试仍只计一个业务 Task。Web 与 CLI 共用一个 request/revision、Run、generation 和账本游标；若后继代次仅更新 Task B，Task A 保留有效分工、attempt 和已获准的完成回执。新代次尚未冻结时，先前的 Judge 判词仍可在其原代次查阅。历史运行缺失获准分母时显示未知。摘要和按需详情区分公开 Board 元数据、读回执与正式签署。[工作台和 CLI 回归](../../tests/test_stage_team_workbench.py) · [Playwright](../../web/tests/workflow-team-live.spec.ts) |
| RM、OA 与独立监督 | 诊断超时沿原案卷继续交接。已启用按需 Reflect 的试点可以关闭常驻 RM Agent；对有明确授权和可核对失败的 Reader，诊断者可建议恢复原专项。RM runtime 复查身份、门槛、版本、预算和暂停，再交给受控动作。重启后按原请求与准入结果去重。Supervisor 和 WRC 沿原入口继续运行。[按需接手测试](../../tests/test_on_demand_reader_diagnosis.py) · [诊断超时和升级](../../tests/test_diagnosis_timeout_handoff.py) · [受控执行、下游与效果](../../tests/test_run_manager_reader_rebuild.py) |

## 读取和恢复

通过 `zf workflow team --request <request_id> --project <project_id>` 查看阶段摘要。增加 `--stage`、`--task` 或 `--member` 可以定位某个阶段、Task 或成员。历史比较使用相同的 `--generation` 和 `--as-of`。这些操作调用现有只读 API。Worker 按 212 的当次输入索引读取证据，读模型提供的成员关系不会授权读取私有消息或工作区。

Task Verify 的专项 pattern 在项目配置中声明为一个可调用的 `fanout_reader`。使用专属 Reader、通用的 `workflow.child.completed/failed`、`wait_for_all` 和 `workflow.invoke.requested` 触发器。这样角色能提交专项证据，正式阶段签署权仍属于对应的 Verifier 或 Judge。对于更正方案，`reuse_operation_id` 必须来自该次 OA 检查点提供的有效意见。规格第 8 节的真实样本、指标和放行判据见[配对验证手册](spec215-qualification.md)。

OA 合成和 Proposal 预览按项目根逐项检查 Plan 成员选择的现有文件。目录需要改为已授权的具体文件或索引；Markdown 标题用与 WRC 相同的规则匹配，缺失或重名会指出成员、义务和输入。提案快照保留当时已见的文件引用，申请新配置或提交时重验，原文件删除后也会阻断。Web 详情页及已准备好的状态摘要显示冻结快照。重新计算提交预览和正式提交会返回当次的检查结果。已准入来源标签、正式作者的报告和重试的冻结快照仍由各自的调度入口绑定。

显式选择的 Plan 成员须从自己本次操作的输入索引读取已分配的证据，再通过受控入口签署。项目的观察模式仍需满足这份显式成员合同。章节引用使用 `path.md#unique-anchor`；章节缺失或歧义、文件变更、权限不符时，记录对应义务、输入和纠正原因。正式作者逐份读取本次扫描中获准的专家报告。[输入与签署回归](../../tests/test_plan_team_inputs.py)

Plan 成员声明的 `requirement` 绑定到该 Run 准入的需求版本。生产入口会传入快照的绝对路径；读取前核对内核的 `workflow.invoke.accepted`、请求 ID、修订、规范快照路径及 SHA-256。校验通过后，调度器将快照作为此 attempt 的必读输入。真实入口和拒绝用例见[规划输入测试](../../tests/test_plan_team_inputs.py)。

### 同一 Run 的规划增员

在 Proposal 的 `stage_teams.plan.reserve_roles` 声明备用的独立 Reader 席位。只有正在执行的 `prd-scan` 能使用这些席位。已有成员提交的报告获准入后，OA 或授权操作者通过原有的 `request-fanout` 动作提交以下字段：

| 字段 | 绑定内容 |
|---|---|
| `stage_id`、`fanout_id`、`workflow_run_id` | 当前 Run 的 `prd-scan` 及其精确批次 |
| `workflow_proposal_digest` | 本 Run 已获准并实际使用的 Proposal |
| `evidence_event_id` | 同一批次的内核 `fanout.child.completed`，可定位本次追加调查的原始报告 |
| `plan_member` | 新义务 ID、备用角色、调查问题、选择理由、完成条件、批准范围内的输入和预期交付 |

Kernel 校验席位和输入范围，写入 `fanout.plan.member.admitted`，再沿正常 Reader 调度路径派工。原有成员继续执行。重启时从账本重建名单，缺派的成员可以补派。正式 Planner 等待本批次全部有效报告，通过自己的工作目录读取每份正文。当前扫描已关闭时，后续变更使用既有的授权代次入口。[增员与恢复回归](../../tests/test_plan_team_extension.py) · [实际工作目录与签署](../../tests/test_plan_team_inputs.py)

### Candidate、Judge 和工作台

Candidate 分工覆盖所有已声明的 AC。跨任务检查拥有独立的检查 ID、涉及任务、验收断言和负责 Reader。Reader 必须为分到的检查交回对应证据；沿旧冻结恢复时按原已批准的合同核验。正式 Judge 从本代必读的 `closure-fact` 取得裁决目标、具名分歧和接手义务。撤回可选复核者须关联前次正式批准的成员及其覆盖去向。[Candidate 合同](../../tests/test_candidate_reader_assignments.py) · [正式 Judge 输入](../../tests/test_goal_judge_admissions.py)

Web 从 Task 打开成员时，按当前 Run、代次和事件游标获取详情。Web 与 CLI 都支持按成员实例或任务筛选 Board 元数据，历史视图保留当时的来源事件、等待与接手事实。[读模型](../../tests/test_stage_team_workbench.py) · [浏览器验收](../../web/tests/workflow-team-live.spec.ts)

### 按需启用 Run Board

在准备 Proposal 前，可在项目配置的 `PrdFlow` 文档中添加以下字段。通用 `zf-prd-v5.yaml` 默认关闭。Workflow Synthesis 选用注册的 v5 模板时，项目显式设置的开关和静默时长会一并写入 Proposal 的有效配置与阶段图。新 Run 使用获批的阶段分工和实际派发的 operation 确定成员及其当前 attempt，部署时无须填写 Run ID 或手工维护实例名单。[注册控制器准备回归](../../tests/test_workflow_registered_controller.py)

```yaml
spec:
  runBoard: {enabled: true, silenceSeconds: 300}
```

成员从各自的 briefing 取得带 `--attempt` 和 `--state-dir` 的命令，加 `--roster` 可以查看当前成员和阶段组。例如，Plan 的 `product-scan` 发现需求与实现约束冲突，可将 `--roster` 换为 `--post scan-conflict --recipient tech-scan --text "请核对 PRD 的并发约束"`，点名当前技术调查席。收件人沿自己的 operation 取阅。使用同一消息 ID、收件人和正文重发时只提醒一次。需要成员正式提交意见或独立验收的阶段，继续按该义务接收交付。

当已加入的团队仍有未完成工作，且超过配置时间没有进展时，Supervisor 结合当前租约与已登记等待判断是否送 Run Manager 诊断。启用 Run Board 不会增加 OA 的派工权限。[新 Run 派发与静默回归](../../tests/e2e/test_prd_run_board_mock_e2e.py)

### 成员会话的查看权限

对需要开放会话的项目，在 `zf.yaml` 设置 `observability.workflow_session_excerpts: true`。Stream JSON transport 在本次调用结束时可发布经过脱敏的最终答复。工作台展开成员后按需检查权限；每次读取都重新核对项目、Run、当前快照、义务、实际实例、operation、attempt 和 provider turn。只读部署、历史快照、已换绑的实例，或缺少可验证发布凭证时均不开放正文。Codex/tmux 的角色级会话路径和 pane 历史不能证明消息归属，不作为查看凭证。[读取入口](../../src/zf/web/workflow_session_routes.py) · [绑定校验](../../src/zf/runtime/workflow_session_excerpt.py)

通用事件详情、列表、Trace、SSE 与 Worker 摘要只投影会话活动的路由和状态。旧 SQLite 索引与缓存中的正文也受此规则约束，底层事件账本仍保留内部证据。[公开投影与历史回归](../../tests/test_event_read_model.py) · [实时 SSE 回归](../../tests/test_web_sse.py)

## 常驻 RM 试点的交接清单

| 本轮需要谁决定 | 可核验的接手与权限 | 逾期或仍未解决时 |
|---|---|---|
| Reader 已耗尽受控重试，需要判断是否重建同一子任务 | A 组由常驻 RM 收到故障上下文。B 组沿已配置的 Autoresearch 或按需 Reflect 接手。Reflect 只能建议与当前失败、operation 和 attempt 匹配的恢复选项。运行时从建议追踪到受控动作，再到重派结果和下游验收。[常驻消费回归](../../tests/test_run_manager_diagnosis_consumer.py) · [按需恢复回归](../../tests/test_on_demand_reader_diagnosis.py) | 空回复或逾期返回原案卷。已有有效的 Owner 等待、受控重试或已确认修复时沿原路径继续。没有获准消费者时明确移交 Owner。[逾期与空回复](../../tests/test_diagnosis_timeout_handoff.py) |
| Task 运行、宿主探针、Candidate 设施故障的原因未知 | RM runtime 汇集原故障和证据，交给已配置的诊断消费者。消费者提供诊断、证据和建议。越出限定修复范围的动作走原审批。[运行故障入口](../../src/zf/runtime/failure_diagnosis_routing.py) · [限定动作与审批](../../tests/test_run_manager_reader_rebuild.py) | 投递失败记录原因和后继请求。首次请求尚未取得有效回复时重用案卷，无业务 Task 的请求保留 Run 身份。[投递与重试](../../tests/test_run_manager_diagnosis_consumer.py) |
| Candidate 对验收条款有争议，或已有 OA 指导仍未取得下游结果 | OA 判断条款和处理策略。若指导回传失败，由跟踪原 Reader 和冻结版本的运行诊断接手，提交下一责任人、证据或审批要求。新的验收范围须经授权。[Candidate 交接](../../src/zf/runtime/candidate_obligation_followup.py) · [冻结诊断回归](../../tests/test_candidate_diagnosis_review.py) | 原判词和故障保留。缺少可采纳的新意见时继续显示等待与责任，避免将一次诊断回复当作覆盖完成。 |
| 诊断者或 RM runtime 失联 | Supervisor/watchdog 独立观察 tick、活动或驻留会话的宿主状态。重启、源码修复等动作沿各自的能力开关与授权。模型的诊断与调度 tick 可分别推进。[独立监督](../../tests/test_run_manager_watchdog.py) · [慢诊断与重启](../../tests/test_run_manager_reflection.py) | 监督者记录超时及缺失的观测。按获准配置执行重启或升级，有合法暂停时保留暂停意图。 |

## 第 7 节的复评口径

- **P0 分工、派发、恢复与裁决。** 通过已有 Synthesis、审批、WRC、Reader 提交和 Judge 准入入口测试。确定性 provider 可核对哪个角色实际收到工作、哪一项结果触发正式判词，以及回放后是否重复派发。未派发角色、重试兄弟成员和失联诊断另测。恢复用例直接核对原故障和后继验收。[派发 E2E](../../tests/e2e/test_oa_stage_team_dispatch_mock_e2e.py) · [独立任务继续推进](../../tests/test_run_manager_independent_attention.py)
- **P1 同快照和权限。** Web 与 CLI 用同一 request、generation 和事件游标比对。已批准、尚无 Task 的 Plan 在真实浏览器里能看到选中成员。分派、运行、产物准入、正式判词和交付分别计算。[读模型与 CLI](../../tests/test_stage_team_workbench.py) · [浏览器真实 API 用例](../../web/tests/workflow-team-live.spec.ts)
- **P2 关系和回放。** 按成员或任务追踪依赖、活动、交接和重试历史；返回当前快照能看到后续记录。在没有有效历史分母、当前冻结判词或证据的视图，保留已知事实与缺失原因。[阶段图和回放断言](../../web/tests/workflow-execution-graph.spec.ts) · [工作台读模型](../../src/zf/runtime/stage_team_projection.py)

## 验收边界

最小 PRD 实跑曾在实施交接遇到托管 Codex hook 出现在写入工作树的问题。linked writer 现从角色私有 `CODEX_HOME` 加载 hooks；Git 回归覆盖干净目录、用户未跟踪 hook 和用户修改过的已跟踪 hook。宿主 Codex 0.172.0 的 `hooks/list` 返回五个已启用且已受信的 hook，worker 的 `git status` 保持干净。[运行时与交接说明](oa-wrc-task-pipeline-delegation.md)

确定性回放检查派发、恢复、结果准入、交接和拒绝路径。Docker Playwright 检查真实只读 API 与浏览器界面。无监工运行的业务覆盖、真实模型质量、审批后实际人工介入和哪一组恢复更快，需要新的批准窗口及独立证据。两组真实试点完成前，当前项目默认角色配置继续生效。


父 Run 已因待处理决定进入静默时，原 Run Manager 请求关联的 Autoresearch 超时仍可把部分诊断送回该决定的负责人。运行时验证原告警、同一 Run 的诊断请求及终止回执，并检查其他待决事项、暂停与终态。已消费的交接保留原回执。单次反思建议继续等待时，新的恢复动作仍需独立授权。[静默与准入回归](../../tests/test_quiescent_autoresearch_handoff.py)


### Candidate Reader 的证据分工

提案可继续只用 `stage_teams.candidate_verify` 的 `width` 和 `reason`。
需要指定不同证据的责任人时，`members` 声明与席位数相同的阶段义务。每席引用现有验收标识
（`acceptance:TASK/AC`、`journey:ID`、`integration:ID`），写明独立核查的问题及预期输出。
例：浏览器席负责 `journey:checkout`，运行时席负责 `integration:overlap`；
Planner 用现有用户旅程与集成断言定义两者，把对应 ID 分给各自的 Reader。

Planner 提交前校验成员、证据 ID 与具体分配。显式声明的每个 ID 必须由其唯一责任席领取。
正式 Plan 包将获准的职责连同角色分配一起冻结，OA 按现有摘要批准。在 Candidate 阶段，
成员收到自己的问题、证据 ID、验收范围与预期输出，通过现有结果准入对同一目标签署判词。
项目批准文字中的专业判断仍由 OA、Planner 与独立 Reader 核对。
