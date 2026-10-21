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

独立验证时点由实际 workflow 决定，不由 `task_verify` 标签决定。
需要短反馈时评估已有 Task Pipeline，或在当前路线中使用可独立验收的小批次；
不能将整个 PRD 的剩余 mandatory 义务静默丢弃。项目技术栈及
`project.scripts.setup` 必须由项目适配明确配置，并以干净 worktree 验证。

回归覆盖两种不同业务的共享规则/分区读取、旧端口、Candidate 全读、遗漏/重复分区、
摘要篡改、真实 handoff、preflight→Critic caller 和只读 CLI。配置、事件、产物和
候选相关 sentinel/mock E2E 另列在任务完成记录。
这些测试证明机械行为与兼容性，不证明真实模型计划质量或业务全链路已交付。
