# Kanban → Judge 真实链路鲁棒性与需求保真验证

本轮按[保存的 Prompt](../runbooks/2026-10-31-0040-kanban-judge-fidelity-e2e-prompt.md)执行了真实 Codex、Docker Playwright 和独立产品验收。**基线 A 在修复后交付成功；修订组 B2 在 Plan 改需求时发现合同冲突，未通过、未进入 Impl，已受控取消。不能宣称两轮全绿，也不能宣称整个链路零精度损失。**

源码基线 `3746d313`，工作分支 `wip/codex-20261031-v5-fidelity-e2e`。私有证据目录 `/tmp/zf-v5-fidelity-20261031-004038/evidence/`；本文的证据文件名均相对此目录。测试 Web 为 `0.0.0.0:8002`，真实 dev/8001 未修改。项目使用自定义 `project.state_dir`，未依赖默认 `.zf`。浏览器运行于 `mcp/playwright:latest`，host networking；未安装宿主浏览器。Docker 使用已有组权限 `sg docker`。

## 样本与结果

| 样本 | 项目 / Task / Run | 配置与结果 |
|---|---|---|
| A 基线 | hello-fidelity-a / TASK-56B1F3 / prepare-TASK-56B1F3-52add73238fbcd8a | 8 lane，业务角色实际 gpt-6.1-sol；最终交付成功 |
| B 首次修订 | hello-fidelity-b / TASK-1C5FFF / prepare-TASK-1C5FFF-a36b9e8d65732070 | 故意选择了阶段不兼容的已安装技能；Preview 漏检，启动失败；保留失败后取消 |
| B2 重试（证据前缀 c） | hello-fidelity-b2 / TASK-9B520C / prepare-TASK-9B520C-0b02d8707e152f8a | 2 lane、实际 scan/Planner/Critic gpt-6-sol；合法 Impl 技能装配成功；Plan 改默认值后合同冲突被 Critic 阻塞，受控取消 |

Kanban 使用真实 gpt-6.1-sol/medium。OA 角色实际为 gpt-6.1-terra；模型字段修改覆盖业务角色，不应据此声称所有 headless/常驻角色也改了模型。`*-actual-provider-roles.json` 基于真实 transcript 的 turn_context，确定性 candidate-integrator 不冒充 Provider。

A 的最终提交为 `aa749e4f10cd7a8e45f807a8001b4c7b8a99dbe0`，候选提交为 `a021de5c5c96cafe1e5b209ae1bc1a33cf901db1`。同 Run 终态链：

- `ship.completed`：`evt-8cc681d2a6a0`，02:00:14 UTC。
- `run.delivery.settled`：`evt-e0f03bfce029`，ship_status=completed。
- `run.goal.completed`：`evt-573a8c957703`，02:00:15 UTC，由 deterministic completion gate 接纳。

`a-delivery-proof.json` 验证 main 指向最终提交、候选为最终提交祖先、相对基线只改变 `hello.py` 和 `tests/test_hello.py`。从候选和最终提交分别 git archive 到独立目录后执行 oracle；精确 stdout/stderr/退出码、空字符串与空白、英文、中文、trim 均通过，实际 unittest **8 tests / OK**。补充 oracle 独立覆盖缺少 --name 值、未知选项的 stderr-only 失败行为；最终源码仅标准库，无网络/Web/持久状态。

## 浏览器与恢复验证

| 操作 | 真实结果 / 证据 |
|---|---|
| 新建项目、Kanban 输入、Ask User Question、Create Task | A/B/B2 均由 Playwright 操作；R04 用 Other 回答精确错误文字，禁止擅加 `error:` 前缀；原始输入与回答在实现前冻结 |
| Workflow 完整 Raw、Agent 修改 | A 只将 plan_approval 改为 true；B2 修改 lane 8→2、model、Impl skills、plan_approval，递归 YAML diff 只有 4 个目标字段及 4 个 pool 派生字段；`c-yaml-locality.json` |
| 无效技能早期拦截 | 修复后 B2 尝试 Impl 绑定 plan/replan/critic 技能，Preview 返回 422、无新 revision/配置写入/Run；合法 done-contract 技能通过 |
| 旧 Workflow 审批 | B/B2 旧 revision 返回 409 proposal_superseded；审批前无 Run；重复有效提交无重复 invoke/Run/runtime |
| Plan 人工门禁 | A Plan `evt-507411c89f34` 由 UI 于 01:14:32 批准，此前无 Impl；B2 原 Plan `evt-0427c9f80bd7` 一直未批准 |
| 全文阅读与引用 Agent | B2 格式化完整 Plan、正文区域约 1472×798；440px 小对话框与 Preview/Raw/Agent 工具栏对齐，输入/Send/Apply 可用；`c-quote-ui-proof.json` 和截图 |
| 局部 Diff 范围 | 引用 SDD 段落，把无参数 world 改为 driver，其他字符及段外全文完全相同；`c-plan-diff-locality.json` |
| 应用后旧 Plan 失效 | Apply 产生受控 plan.rejected/replan；旧 Approve 禁用，正确格式重放审批返回 409 stale_decision；`c-stale-plan-approval-valid.json` |
| 安全检查点重启 | B2 待 Plan 审批且无活动 Provider 时停止，再通过浏览器重复提交恢复；invoke/goal/admission 均仍为 1，runtime start 为 2，配置及 Plan markdown/refs 不变、Impl=0；`c-restart-invariants.json` |
| Judge 证据修复后恢复 | A 保留首次 blocked；安全重启后通过既有 ControlledActionService stage-retrigger 生成新 closure identity；同 Judge session 新 attempt 重新读取/裁决通过 |
| busy 超过 600 秒 | A Discovery 实际执行 707.44 秒；639.7 秒时最近活动仅 1.91 秒，仍 busy、timeout_seconds=0；`a-busy-over-600-flow-discovery.json` |
| 动态图与完成显示 | 运行中 Judge 重试及两条执行记录可见；终态 UI 显示“已完成 / 来源：内核终态事件”；`a-11-judge-retry-dag.png`、`a-12-delivered-graph.png`、browser trace |

stage-retrigger 不在 Web allowlist，浏览器尝试返回 404；恢复使用已有受控服务，记录 control.action.requested 与结果，未新增 Web 权限，也未直接写 canonical state。其他产品提交、审批、局部修改、取消都经浏览器 UI/API。若干 locator、错误 envelope 和接口路径尝试属于 harness 操作错误，未当作产品回归。页面 JS error 数为 0。页面刷新后需等异步投影加载，不能把瞬间“未加载”当成执行状态。

## 需求 × 阶段矩阵

以下“保留”指核对完整语义与对应证据，不以 ID 出现代替覆盖；输入 witness 独立列在下一节。

| 需求 | Task / Workflow 来源 | A Plan → Impl → Verify → Judge → 交付 | B2 首版 Plan | B2 引用修订后 |
|---|---|---|---|---|
| R01 默认精确问候 | 保留 world 与换行、空 stderr、码 0 | 全部保留；独立 oracle 通过 | 保留 | 正文/任务 AC 改为 driver，mandatory claim 仍 world：**冲突，阻塞** |
| R02 英文 | 保留 | 全部保留，三元组精确通过 | 保留 | 保留；未执行产品验证 |
| R03 中文 | 保留 | 全部保留，三元组精确通过 | 保留 | 保留；未执行产品验证 |
| R04 trim / 空白失败 | 用户澄清准确保留，无 error: 前缀 | 空白/空串/trim 实测通过 | 保留 | 保留；未执行产品验证 |
| R05 失败 stderr-only | 保留，禁止默认回退 | 空白、缺值、未知参数均实测通过 | **弱化**：非空白解析失败测试被列为可选，Critic 拒绝并自动修复为必测 | 必测失败路径仍保留；默认值引用受 R01 合同冲突牵连 |
| R06 两文件 / 标准库 / 无网络 | 保留 | commit diff、imports 和实际行为核对通过 | 保留 | 保留；无产品实现 |
| R07 真正执行 unittest | 保留，禁止零测试 | 实际 8 tests / OK；独立重跑通过 | 经 R05 返修要求至少 7 个必测场景 | 保留；未运行 Impl/Verify/Judge |
| Workflow + Plan 人工审批 | 原文保留，但候选默认 plan_approval=false，需 Agent 显式修改 | 实际双审批完成；首次 Judge 缺 Workflow 审批证据而阻塞，补齐后通过 | 门禁保留 | 旧 Plan 不能再批准；未批准矛盾新方案 |

B2 首次 R05 弱化：`evt-fd5d30ee3d35`，01:34:34 UTC；原 Plan/task map 与拒绝工件分别为 `c-first-plan-before-rework.md`、`c-first-task-map-before-rework.json`、`c-first-plan-critic-rejection.json`。返修后明确要求空白、缺值、未知选项三种失败独立验证。

B2 引用修订的冲突：`c-owner-revision-conflict.json` 直接并列 mandatory R01=world 和 AC-R01=driver。Critic `evt-d57c5782d201` 正式 rejected，指出 owner 已明确 driver，无需再问同一产品问题，应由受控机制更新 Requirement/claim 绑定。`c-owner-revision-critic-blocked.json` 保留全文。Kernel 随后又尝试 Planner rework；本次通过 run-cancel `evt-3c95816a0c74` 结束已复现负例，未放宽 claim 防弱化校验。**这不是修改能力已闭环的成功样本。**

## 输入保真与 unknown

A 最终 71 个冻结 source version 的 digest 全部一致。critical-context 中 2 条用户消息与 ledger 原文完全相同；UI 仅移除提交文本外层末尾换行，不能把这个格式差异报告为语义丢失。完整 Requirement 的 objective、澄清和 Task 绑定都被保留，不拿缺少 behavior 的 task-input-contract 替代 PRD。

必读分页机械覆盖均完整，但 Provider 完整输出见证不是全绿：

| A 阶段 | 必读 source 数 | 机械缺页 | Provider 完整见证未证明的 source 数 |
|---|---:|---:|---:|
| product/tech scan，各自 | 5 | 0 | 0 |
| Planner | 3 | 0 | 0 |
| Plan Critic | 9 | 0 | 0 |
| Impl | 13 | 0 | 0 |
| Task Verify / 各 Candidate Verify | 15 | 0 | 0 |
| OA shadow | 3 | 0 | 2 |
| Discovery | 41 | 0 | 39 |
| Judge 第一次 | 31 | 0 | 0 |
| Judge 重试 | 31 | 0 | 30 |

OA 38、Discovery 117、Judge 重试 50 条 read-ledger row 为 unknown。旁路嵌套输出审计只为 OA 找到 2 条完整包匹配，未修改历史收据。Judge 重试实际将 `required-artifact-page.v2` 解析后只输出 `source_id` 和 `content`，正文在 transcript 可见，但 attempt/bundle/分页包整体没有原样输出，现有 witness 无法将其认定为完整包。不能把 unknown 等同于全部内容丢失，也不能反过来宣称已证明无损。

证据：`a-context-proof.json`、`a-read-policy-coverage.json`、`a-unknown-witness-audit.json`、`a-judge-output-budget-samples.json`、`a-judge-filtered-output-samples.json`。B2 的 scan/Planner/Critic 已封存必读见证完整，OA 仍有未知。语义理解本身也不能由 digest 或可见性证明。

## 并行、时长与开销

A 只有 1 个紧耦合实现任务，Impl width=1 合理，不能用本样本证明多 Impl lane 并行。Candidate Verify 实际 8 个 operation 时间区间重叠，峰值并行度 **8**；各自 403–524 秒。B2 已装配 2 lane，但停在 Plan，**未验证其 Impl/Verify 并行**。

A scan 139/157 秒、Planner 198 秒、Plan Critic 192 秒、Impl 266 秒、Task Verify 210 秒、Discovery 707 秒、Judge 首次 259 秒、重试 302 秒。整体时间包含人工审阅、故障诊断和重启，不当作纯模型执行 benchmark。

`a-execution-proof.json` 的项目用量账本中 verify-lane 汇总 fresh input 773,764、output 51,525、cache-read 16,475,008 tokens（Task Verify 与 8 次 Candidate Verify 合计）。这是累计用量，不是单次上下文占用，不据此直接推算账单。最小任务仍支付固定 8 路候选验证和全链路包的成本；本轮没有因 600 秒累计时长或 token limit 截断业务执行。后续优化应优先按任务/风险选择验证宽度、减少重复配置正文，并保留完整来源和独立验收。

## 已实施的 4 个修复

1. 聊天任务无独立 source_ref 时，校验 confirmed Requirement 的 Task 合同 digest、身份、覆盖和项目路径，再物化完整不可变 Requirement。显式 source_ref 优先，无效仍拒绝。同步 synthesis skill 三份分发与提示。
2. Raw YAML 的 contextWindowTokens 纯数字错误脱敏：仅加入已知数值字段白名单，真实密钥/混合秘密仍遮盖。B 的实际值是 120000，模型修改没有改变它。
3. Preview/Apply 漏检角色技能阶段：复用 `zf start` 的 validate_skill_sources，在 strict preflight STOP、Apply 写入前重新检查；非 strict 保留 WARN。
4. Goal closure-fact 漏 Workflow 审批：按 invocation 所绑定 Request/revision/Requirement digest 投影实际 approved/submit/invoke 事件，保留主体与顺序，排除错 Run、旧版和晚审批。A 旧 blocked 保留，修复后新 closure identity 实际 Judge 通过并交付。

## 尚未修复与复测入口

- **P1：Plan 引用修改产品要求的正式修订协议。** 不能仅允许改写所有 mandatory claims。需要有明确受影响 claim ID、旧/新合同摘要、已应用 review ref、owner 批准和新 revision 的受控修订；同步 Requirement、claim set、Plan/Task Map/AC、后续 Verify/Judge。未改条款仍禁止弱化，旧审批/旧结果仍失效。复测使用本轮 B2 的同一 world→driver 案例，必须最终独立 driver oracle 通过。
- **P1：完整 Provider 输入见证。** 先规范 agent 原样输出分页包，或给结构化拆包增加严格绑定 attempt/call/cursor/digest 的适配；保留截断、argument echo、旧 attempt 的负例。修复后重跑 Discovery 和同 session Judge 重试，不回填旧 unknown。
- **P1：显式 Plan 人工确认要求尚未自动成为初始 YAML 门禁。** 本轮均在 Preview 手动改成 true 才启动；测试未覆盖 unattended 场景下防止该约束被漏装配。
- **P2：基于风险/任务规模选择 Candidate Verify 宽度。** 本轮证明并行有效，同时证明 Hello 任务采用 8 路开销偏大；需要独立设计与费用/覆盖对照，不能删验证换低耗。
- 安全检查点重启已验证；活动 Provider 强杀恢复、真实等待 600 秒 idle、重复大样本可靠率未验证。Plan review packet 曾标记 incomplete（结构化决定登记未绑定），虽然完整正文可读并有真实审批，不把它写成 review packet 全绿。

## 验证范围

以下计数有重叠，不相加冒充独立测试总数。所有命令从源码 worktree、使用 `PYTHONPATH=src /home/uisee/workspace/zaofu/.venv/bin/python -m pytest ... -q --no-cov` 执行：

| 范围 / 文件 | 结果 |
|---|---|
| context_delivery_integrity、artifact_read_batch_cli、task_plan_consumption、fanout_progress_liveness、completion_honesty、workflow_preview_review、workflow_review_controller | 116 passed |
| workflow_controller_chat_source、workflow_registered_controller、workflow_request_acceptance、workflow_review_controller | 28 passed |
| skill provenance、workflow synthesis/candidate 相关闭包 | 73 passed / 1 skipped（fixture 依赖的临时示例缺失） |
| redacted_diagnostics、web_workflow_proposals | 16 passed |
| workflow_preview_skill_admission、workflow_review_controller、workflow_config_apply | 22 passed（新增 Apply 测试前的一轮） |
| workflow_preview_skill_admission、workflow_preview_review、workflow_preview_blocked、workflow_blocked_preview、prd_e2e_residual_eight、flow_smoke_e2e | 43 passed |
| goal_closure_lifecycle_evidence、goal_closure_identity、goal_closure_runtime、goal_closure_result、goal_completion_authority、control_actions_recovery | 48 passed |
| prd_e2e_residual_eight、flow_smoke_e2e、goal_delivery_recovery | 15 passed |
| e2e/test_kanban_plan_approve_mock_e2e.py、e2e/test_workflow_runtime_autostart.py | 2 passed / 6 host cases deselected；真实自动启动另由本次浏览器运行证明 |
| bash scripts/dev-premerge-gate.sh（最终 B07 后） | 51 passed |
| frontend npm run build（含 typecheck） | 通过，47 chunks，最大 458106 bytes |
| git diff --check | 通过 |

新增来源、脱敏、技能和审批证据用例均保留先失败再通过记录。`scripts/dev-verify.py plan --base dev` 保守选择 354 个文件并升级为完整 deterministic suite；依仓库“按边界闭包，不因数量跑全量”规则，执行上表聚焦闭包和真实 E2E，**未跑 full pytest**。计划输出为 `dev-verify-final-plan.txt`。

## 收尾与交付物

浏览器 trace、截图、不可变输入、原始 oracle、失败结果、修复后结果、版本/分页审计、产品 Git bundle 和 runtime 归档保留在私有证据目录。`cleanup.json` 记录 simulation.done、停止本轮 runtime/Web/容器、归档后移除运行状态；源码 worktree 保留。测试自动启动的独立 Autoresearch 诊断 checkout 也停止并归档，生成的候选资料保留，不强制删除脏 worktree。

本轮测试总体 **未全部通过**，原始活动任务保留未完成项，不将 B2 的交付验收归档为 done。已实现修复可单独 review；没有 merge/push，也没有重启或改动 dev 的 8001。
