# Plan 执行一致性与按任务交接

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

Web Plan Preview 提供审批概览、模块规格、验收场景、完整原文四种阅读视图。
只请求当前 Plan 已绑定的 task_map/execution_spec/source_inventory 附件 API；
服务端既有路径与 SHA-256 校验继续生效。没有新端口或业务写请求，原文下载保留。
旧 Plan 可以按任务查看 AC、命令、页面、依赖与待决事项；缺少独立规格/场景明确显示，
不自动推断字段、生成测试结果或声称需求覆盖。附件错误、未知任务及错 AC 引用可见，
切换项目/Plan 或关闭 Preview 会取消旧请求，避免迟到数据污染。

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
