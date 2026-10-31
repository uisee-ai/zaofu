# Plan 执行一致性与按任务交接

规格变更后的评审影响、产品收口分类及运行指南见
[规格生命周期与可复现交付](spec-lifecycle-and-delivery.md)。

本改进将通用方法与机械事实分开：Planner/Critic 判断任务语义、SDD/TDD 充分性、
真实依赖和反馈时点；Kernel 保持身份、摘要、范围和必要输入检查。

## 新项目默认值

`zf flow draft` / 项目初始化生成的 runtime profile 启用
`workflow.impl_self_check_required: true`，并将
`workflow.work_units.split_quality.max_acceptance_criteria` 设为 0（不设数量上限）。
验证表面及路径隔离保持原约束。既有 zf.yaml 不被迁移，显式数量上限仍强制执行。
该默认值不启用 v4、不改变 Plan 人工审批，也不提供自动的业务拆分 verdict。

## 实际执行事实

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
