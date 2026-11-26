# Plan 执行一致性与按任务交接

决定登记完整性与待审批问题分开投影：摘要绑定的有效空登记可视为材料就绪，
缺失、坏结构或摘要不符不视为就绪。完整 Task Map 登记保留待决定、待落实和已关闭
分类；它可纠正已关闭问题的显示，但不能给当前审批动作新增问题。`blocking_impact`
描述不覆盖显式 `blocking: false`，未来决定继续按受影响阶段处理。

规格变更后的评审影响、产品收口分类及运行指南见
[规格生命周期与可复现交付](spec-lifecycle-and-delivery.md)。

本改进将通用方法与机械事实分开：Planner/Critic 判断任务语义、SDD/TDD 充分性、
真实依赖和反馈时点；Kernel 保持身份、摘要、范围和必要输入检查。

验证命令的 POSIX `PYTHONPATH` 按冒号分隔逐项核对仓库路径范围。例如
`PYTHONPATH=app:app/tools` 引用两个目录，不是名为 `app:app/tools` 的文件。
裸目录名和带空格的引用目录也需核对；未声明的分量仍拒绝。此解析不扩大任务
写入域，也不把其他含冒号的参数或环境变量自动解释成路径列表。

## 新项目默认值

`zf flow draft` / 项目初始化生成的 runtime profile 启用
`workflow.impl_self_check_required: true`，并将
`workflow.work_units.split_quality.max_acceptance_criteria` 设为 0（不设数量上限）。
验证表面及路径隔离保持原约束。既有 zf.yaml 不被迁移，显式数量上限仍强制执行。
该默认值不启用 v4、不改变 Plan 人工审批，也不提供自动的业务拆分 verdict。

## 实际执行事实

当 Candidate Verify 的命令与启用的宿主容器验证白名单匹配时，Plan 提交与
Critic 派发前会按宿主收据读取器的规则检查 `producer_paths`。每一项都应是
实际产出的相对文件名，例如 `ZF_EVIDENCE_DIR/desktop.png`。目录声明会返回
`host_producer_path_invalid` 和命令所在字段。冻结前仍需对照生产脚本核实
文件名和业务用例；文件是否真实生成、内容能否支撑条款，由后续采证与独立 Verify 判定。

历史 Candidate 已因旧目录声明阻塞时，owner 可在 `stage-replan-new-generation`
的基础设施修复请求中附上 `candidate_media_review_ref`。该不可变审批材料绑定失败事件、
冻结候选、原宿主回执、完整原命令和已复核的命名文件。运行时保留既有命名文件义务，
核对归档与原始执行身份，只向此次受控动作派发的新 Reader 提供映射和来源材料。
补充测试应注明其独立测试版本与适用范围；原宿主回执、旧 Reader 判词和产品门禁均保留。

```bash
zf plan inspect-execution --config zf.yaml --task-map path/to/task_map.json --flow-kind prd
# 已准入运行可补充 --plan-package <配置 state_dir 内的 Package JSON>
```

命令只读配置与工件，不创建运行状态或批准计划。输出包含依赖链/分层宽度、
实际验证触发阶段、Task Pipeline 模式、自检强制项、setup 声明状态及 JSON 输入字节数。
字节数不是 token 数，链长不是耗时；报告没有 passed 或 semantic verdict。
未给 Package 时，只能计量显式给定的 Task Map，不能称为全部输入大小。
CLI 使用当前指定配置（输出明确标记），不能替代在途运行冻结配置；运行时
Planner/critic 的事实使用当前派发配置及冻结的验证请求。

相同事实接入现有 Plan candidate preflight，并作为不可变
`plan-execution-review` 输入交给独立 Critic，纳入必读账本。
结构 preflight 的 pass/fail 继续仅由结构错误决定；事实报告中的待评审项
不会自动阻断、批准、调整 task map、启用 pipeline 或执行 setup。
Planner 的 validate 结果与 Critic 的输入使用相同事实生成器。

### Critic 的执行对账

新冻结的 `plan-execution-review.execution_comparisons` 是本轮待对照的执行事实。
`standard_with_empty_write_scope` 列出实际进入标准流程且显式声明空写集的任务。
新评审的 `facts.entries[]` 提供声明与调度入口、起始阶段、允许路径和本任务登记的命令 ID。
Critic 对照原始需求确定这一入口是否合适。返工指向作者能修改的候选字段，重新验证候选时由运行时生成新的摘要。
`candidate_command_scope` 列出当前按 Candidate 选择或声明的命令、登记任务与命令 ID，
以及仅由宿主许可的命令。Critic 从冻结需求确定本轮必须取得哪些独立回执，再与
当前 Task Map 的登记对照。独立构建与“构建后执行浏览器检查”可以在同一需求中分别出现。
仅获许可的命令可以不选；合法的空写入范围也可以通过。

使用 `current-evidence-v1` 的纯 Critic 在 full 审查（含默认 full）批准时，
若必读摘要包含这些检查项，在 `report.execution_assessments` 为每个 comparison 提交
一项 conclusion 和既有 basis。按摘要列出受影响的 task_ids，引用冻结需求
（或其显式输入）以及当前候选来源的 digest、JSON 选择器和原值或原文。
运行时核对检查项覆盖与引用绑定；是否满足需求仍由 Critic 判断。合法计划可保留
空 findings 和 gates。退回修订继续使用阻塞项的既有证据合同；再次完整批准时
依当前摘要重新对账，上一轮阻塞项已修好只证明该项修好。

targeted、已冻结的局部或无变更复审，沿用各自批准的覆盖范围。旧快照不含
execution_comparisons，恢复时复用原摘要及原 verdict 合同。Task Map 或已绑定配置
发生变化时由新请求冻结新的对照事实。

阻塞式 Task Pipeline 的执行预检在只读任务的 `verification.task_requirements` 明细中提供
`initial_readonly_verify`。其中列出声明的基线、运行时选择的验收 owner、当前 mandatory AC
及命令 ID。Planner、Critic 与 Owner 可以在批准前对照基线已有实现及最终候选的验收矩阵。
同一投影通过 `zf plan inspect-execution` 和 Critic 的 `plan-execution-verification` 明细交付。
缺少明确 AC ID 的历史草稿需在派发合同生成后核对；阶段可行性由评审者判定。

## 显式分区规格

补充规格端口可声明如下正文（既有固定 schema 矩阵不要改套此结构）：

```json
{
  "schema_version": "task-scoped-plan-input.v1",
  "shared": {"rule": "共同协议、来源与同版决定"},
  "tasks": [
    {"task_id": "T-publish", "body": {"action": "发布行为、反例和依赖接口"}},
    {"task_id": "T-index", "body": {"action": "索引行为、反例和依赖接口"}}
  ]
}
```

在正式 Plan 的 `plan_ports` 提交这个工件，并把对应逻辑端口列入
`required_plan_ports`。使用准确的派发 Task ID；身份改变时受控重规划并更新分区，
不猜测别名。共享内容由作者显式填写，不由 Kernel 按关键词筛选业务语义。

准入前检查每个任务分区存在、无重复及未分配顶层内容。实际 handoff 再校验
Package 身份/摘要，并把 Impl/Task Verify 的必读路径设置为
`$.shared` 与对应 `$.tasks[index]`。候选验证仍读 `$`。
整个源文件及原摘要保留，不生成第二个 canonical task contract，不删除其他任务正文。
受控读取拒绝越出声明路径和摘要漂移；旧端口继续全文读取。

这会降低显式分区端口的必读量，但不会自动压缩旧 Task Map、旧矩阵或已有项目。
作者仍须证明 shared/本片内容充分；不得借分区隐去全局安全、权限或最终验收义务。

## 实施与验证边界

同一份 Plan 中，任务专项验收和候选整体验收可以分属不同 Owner。OA 返工指令与
Impl 交付必须沿用冻结 AC 的 `verification_owner` / command id / 产物归属，不能将
候选全量命令提升为所有前置任务的交付前提。例如脚手架的专项检查已通过，但全量
命令缺少由后续装配任务提供的入口：保留真实失败与后续 Owner，最终候选仍须通过该
命令；当前任务自身缺失的必需产物或检查则仍阻塞。指令冲突由 OA 修订或受控重规划，
不通过改写旧 verdict、扩大当前任务路径或增加重试预算处理。
方法入口为 `zf-yoke-orchestrator-role-context` 与 `zf-harness-done-contract`；不新增
Kernel 的项目语义判定，也不让 worker 自行豁免当前合同的失败。

独立验证时点由实际 workflow 决定，不由 `task_verify` 标签决定。
需要短反馈时评估已有 Task Pipeline，或在当前路线中使用可独立验收的小批次；
不能将整个 PRD 的剩余 mandatory 义务静默丢弃。项目技术栈及
`project.scripts.setup` 必须由项目适配明确配置，并以干净 worktree 验证。

回归覆盖两种不同业务的共享规则/分区读取、旧端口、Candidate 全读、遗漏/重复分区、
摘要篡改、真实 handoff、preflight→Critic caller 和只读 CLI。配置、事件、产物和
候选相关 sentinel/mock E2E 另列在任务完成记录。
这些测试证明机械行为与兼容性，不证明真实模型计划质量或业务全链路已交付。

## 产品规格与分批设计

Planner/Critic 通过既有依赖物化加载
[规格与设计就绪方法](../../skills/zf-plan-execution-readiness/references/spec-design-readiness.md)。
产品规格保留有来源的用户故事、价值优先级、独立可演示结果与成功判据；
技术设计先细化当前实施前沿需要的数据、接口、事务/幂等和 UI 状态。
公共合同保留生产者、消费者、版本/同版锚点及使用前的决定关闭证据。
原始效果图、候选模型、已采纳合同和执行证据分别记录，未定义指标不得猜测。

Critic 在已有评审工件分别记录产品、设计、来源覆盖、执行验证、未决治理的结论、
正文位置、反例和影响任务；有界返修必须说明复用的结论及未复审范围。
这些是 Skill 方法，不是新 Task schema 或 Kernel 内容评分器。未来事项不共同
阻塞首片；未知公共协议则不能由并行消费者各自猜测。

Preview 仅传递显式的 `derived_plan_full` 声明。缺失声明时，审批绑定和可读正文
使用 `derived_plan`（需核对完整性），没有正文/声明时沿用 `unknown`；
全文可读不代表语义完整。显式摘要/完整声明和既有审批动作保持兼容。
历史事件/Bundle 不被重写，也无法凭投影修复追认旧 `derived_plan_full` 的出处。
Workflow Graph 的持久缓存代际升级，避免旧推断结果及 ETag 遮蔽新投影。

采用本批代码需重启 Web/API 消费者；Skill 更新需在后续 Planner/Critic 会话重新
物化。已有 ls Plan 必须走受控新版本修订才获得补充规格，不因更新 Skill 自动改变。
本批切片验证、限制与 ls 修订交接见
[验证记录](../records/2026-10-31-sdd-readiness-validation.md)。


## 同版 Plan 人工审阅

审核包的 Provenance 按 `workflow_run_id` 和 `pdd_id` 分别核对事件、bundle 与
已绑定工件（含 metadata）。Run ID 与 PDD ID 可以不同；同类标识不一致仍报
`artifact_lineage_mismatch`。此只读检查不修改计划或批准状态；回归见
`tests/test_plan_review_packet_lineage.py`。Workflow Graph 的持久缓存代际包含
投影实现版本；审核包语义变化时同步更新代际，避免旧 SQLite 结果和旧 ETag
继续隐藏修复，完整回归见 `tests/test_workflow_workbench_cache.py`。

Planner/Critic 的[人工审阅方法](../../skills/zf-plan-execution-readiness/references/human-review.md)
把审批概览、模块规格、业务旅程和 Given/When/Then 场景放入既有
`execution_spec.shared.human_review` 与 `tasks[].body.human_review`；Task Map
仍是唯一派发合同。语义完整性由 Agent 评审，不新增 Kernel 业务判断。

Web Plan Preview 提供审批概览、模块规格、页面与设计、验收场景、待决事项、覆盖审查、
完整原文和来源原文。左侧导航及模块查找用于按产品阅读；工程上下文按需展开。
原文提供真实章节索引、正文搜索定位、连续/分页阅读及下载，视图/任务/来源/章节
保存于当前 Plan 的 URL fragment。手机界面保留相同入口与独立滚动区域。
只请求当前 Plan 已绑定的 task_map/execution_spec/source_inventory 附件 API；
覆盖/场景视图按需增加 source_index、coverage_ledger、acceptance_matrix、test_matrix。
服务端既有路径与 SHA-256 校验继续生效。没有新端口或业务写请求，原文下载保留。
旧 Plan 可以按任务查看 AC、命令、页面、依赖与待决事项；缺少独立规格/场景明确显示，
不自动推断字段、生成测试结果或声称需求覆盖。附件错误、未知任务及错 AC 引用可见，
切换项目/Plan 或关闭 Preview 会取消旧请求，避免迟到数据污染。

模块切换以 Task ID 重建正文，避免静态 Markdown 渲染器保留上一个任务。
页面支持多个 task_ids，人工场景兼容 name 和同版 source_ids，正式验收矩阵单独显示。
决定登记从摘要校验通过的 Task Map 读取，通过 `decision_review` 返回内容、读取状态和
total/pending/blocking/follow_up/resolved/unknown 分类；blocking 是显式阻塞数，
可与其他分类重叠。原有 `owner_decision_items`、`decision_count` 和审批 action 不变。
读取失败明确展示，不能拿审批问题列表的零值代表完整登记已关闭。

覆盖审查每页最多 25 条，支持搜索和未分配、待决定、引用缺口、未验证筛选。
从同版来源链接到 Task、AC、命令及原始证据状态；未读取工件与无效 ID 分别提示。
只为已绑定附件提供可读链接，未绑定路径显示原始引用，不开放任意文件读取。
参考图 ID 与设计/实现/视觉验收状态照录；阅读器不生成设计或推断截图位置。
版本比较需在同一项目内输入另一个 Plan ID，且两者有一致的非空 trace_id 或 pdd_id；
校验并读取两版已绑定来源/覆盖工件，列出条款增删、正文、状态、Task/AC/命令分配变化。
比较不涵盖完整规格正文差异或实时执行状态；缺工件、摘要错误和身份不明均显式失败。

采用变更时，先构建 web 静态资源，再重启使用新代码的 Web/API 消费者并刷新浏览器。
Planner/Critic 新会话须从同步后的 skill 分发副本加载方法；在途会话不会自动获得新方法。
不需重启已有 workflow 来阅读旧 Plan，也不会因此修订已批准 Plan。
ls 的已知语义错配及后续受控纠偏见[审阅记录](../records/2026-10-31-ls-plan-coverage-review.md)。

该改进不会改写已接纳 Plan，也不会给旧 Plan 自动补出新生成的业务规格。
完善既有 Plan 的语义内容仍需受控修订、独立评审及人类审批。规划命令与场景都是
未来执行义务，不能当作真实测试通过的证据。

阅读器回归入口：先 `npm --prefix web run build`，再启动
`uv run uvicorn tests.plan_preview_app:create_test_app --factory --host 0.0.0.0 --port 8003`；
必须先构建再启动 API，确保挂载该次静态资源。使用 `mcp/playwright:latest` Docker
容器、host network，设置 `ZF_WEB_BASE_URL=http://127.0.0.1:8003` 后执行
`playwright test --config=playwright.plan-review.config.ts`。该配置仅选择阅读器相关
浏览器用例，单 worker，断言等待 60 秒/用例 120 秒（含 12,000 行压力样本）属于功能测试启动预算，不是产品
性能验收指标。fixture 使用隔离临时状态，退出发出 `simulation.done` 并清理。

覆盖引用编辑测试时，将临时 API 的 `ZF_WEB_ACTION_TOKEN` 设为 fixture 使用的
`plan-agent-fixture-token`；只在该隔离测试服务使用。Docker 将源码与 node_modules
只读挂载，测试输出设为容器内 `/tmp/zf-prd-review-results`，避免 UID 差异导致写入失败。
本批验证范围与已复现的基线失败见[验证记录](../records/2026-10-31-prd-reader-validation.md)。

### 从讨论到真实 Plan 的 UI 验证

[PRD UI 验证方法](../../skills/zf-verify-zaofu/references/prd-ui-journeys.md) 分别从
Kanban 独立讨论和 Channel Group 进入。测试使用预先启用 `plan_approval` 的 PRD v5，
经 UI 确认 PRD、创建 Task、审阅 YAML 并启动，然后预览、刷新和下载本 Run 生成的 Plan。
fake 只替换模型输出，准入、身份和审批保持真实；真实 Provider 使用新的隔离项目执行。

Channel 从已结束回复的 conversation 切到 multi_lens 时，UI 发起既有受控 restart；
在途/排队回复仍阻止切换。新 Task 提示使用 `prepare_workflow=true`，项目目标目录在
提案编译时从可信上下文绑定，Owner 显式选择的目标保留。Task 与 Workflow 启动分别确认。

Plan 综合准入通过候选摘要核验后，`admit_synthesized_plan_package` 将结果显式声明的
`plan_content_profile` 传给聚合产物及 `task_map.ready`。缺失声明不推断完整性，摘要
不升级为完整 Plan。回归覆盖 `tests/test_plan_content_profile.py` 的实际聚合调用方，
浏览器测试继续核对同版产物、全文下载及审批前无实施派发。

纯聚合 Critic 的 `plan_content_profile` 是 verdict 元数据，不是留空的计划结构正文。
`zf-yoke-critic-role-context` 规定其覆盖判定和证据要求，实际 fanout 简报明确该输出字段；
Kernel 继续只传播显式声明。真实审查通过但遗漏声明时，页面保留普通 Plan 标记，
完整 Plan UI 验收失败；测试不得自行补值。方法更新只作用于后续加载该 Skill 的会话。

在线 Apply 改变执行配置后，已启动 consumer 仍可能持有旧配置而拒绝新 Run；本验证
不绕过 `consumer_config_stale`，也不把启动前配置的成功结果当作热重载验证。

### 独立 Critic 在本次候选内核证执行异议

完整审查遇到只读 Task Pipeline 入口时，冻结摘要分别列出每项任务的首站、实际选取的命令和验收，
以及混合任务间的显式依赖。比较的 `before_rework` 标记适用于当前候选。独立 Critic 在
`validate-plan` 和正式提交时用 `execution_assessments` 对这些比较逐项裁决。
每项引用当前冻结摘要中与争议直接相关的子字段、需求和候选证据。`repair_required` 对应有同名 comparison
的修复项，`no_repair` 保留解释及反证，证据不足的 `needs_clarification` 关联 Owner 决策问题。运行时检查选择事实、证据绑定和覆盖，语义由 Critic 判断。

拒绝后的返工读取 `critical-reader-repair-context`。其中 `rework_feedback` 给出已确认的修复项；
`plan_review_dispositions` 按拒绝包的 digest 附上本次裁决及其证据，供 Planner 了解已撤回意见及影响范围。
完成当前裁决只结算当前独立评审，一次失败仍遵循现有因果幂等的返工路由。新候选的审批仍需独立裁决。
