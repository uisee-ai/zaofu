# 项目显式关闭资源上限

`zf.yaml` 可按范围选择零值，未配置的项目继续使用原默认值。

- `autoresearch.trigger_policy.cooldown_minutes`、`max_triggers_per_hour`、
  `max_daily_runs` 为 `0` 时关闭对应冷却/次数上限。功能开关、问题分类、事件去重仍执行。
- `runtime.autoresearch_resident.max_actions_per_tick: 0` 处理当前全部待执行动作；
  检查间隔仍是正数，实验自己声明的预算和执行授权不变。
- `taskPipeline.integrationAdmission.riskReview` 的 `timeoutSeconds`、`maxTurns`、
  `budgetUsd` 可为 `0`。这不启用风险审查；显式全零 operation limits 不回退 profile，
  `budgetUsd: 0` 不再被结果检查解释成禁止任何费用。
- `roles[].provider_session.max_parallel_agents: 0` 关闭 ZaoFu 原生子 agent 结果
  数量上限。该值从角色配置冻结到 Operation，结果自身不能声明豁免。未设置时保留 6，
  原有正数范围 1–6 保持不变。零值不向 Provider CLI 传递并发参数，实际 Provider
  配额仍由 Provider 决定；计数一致性、身份、终态结算、证据检查仍执行。
- Research root 的结果汇总使用已派发 manifest 子项 payload 中的
  `provider_native_max_children`；零值关闭该数量上限，缺省继续使用 pilot 的 4。
- 研究角色的 `budget_usd: null` 关闭角色费用上限；阶段 `timeout_seconds: 0`
  关闭研究阶段总超时。停滞观察与进程启动等待是不同控制项。

旧 Run、Operation 及 Task Pipeline profile 的冻结身份不因配置文件变化自动改写。
既有 blocked operation 必须沿自己的受控恢复路径处理。

兼容已准入 Task Pipeline：仅当风险审查仍关闭、三个额度从历史默认
180 秒/1 轮/1 USD 全部变为 0，且还原这些默认值后的完整摘要精确等于
已准入摘要时，运行时可绑定同一个旧身份。此变更没有执行效果，绑定结果
通过 `policy_binding_resolution` 同时记录配置和已准入摘要。启用审查、
修改其他策略或部分改值仍拒绝；原事件、任务代际和 Operation 请求不改写。

验证入口：`tests/test_optional_resource_limits.py`，以及 Autoresearch trigger/resident、
provider session、result admission、workflow budget 和 Task Pipeline profile 的直接调用闭包。
实施记录：[资源上限任务](../../tasks/active/2026-11-01-0011-optional-resource-limits.md)。
