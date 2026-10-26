# 以实际计量复核执行预算

读取 `plan-execution-review.usage_observations`，或运行只读命令
`zf plan inspect-execution --config <zf.yaml> --task-map <task_map.json> --flow-kind <kind>`。
需要固定版本时加 `--plan-package <已准入包>`。该事实报告不批准或修改配置。

1. 先核对 scope、workflow_run_id、operation_id、attempt_id 和来源事件。
   `project_history` 不是当前 Run 的消费；缺少阶段或 attempt 归属时明确 unknown，
   不按角色名称猜测重试次数。`available=false` 不表示零用量。
2. 输入、输出、cache-read 和 cache-creation 是既有 CostTracker 的非重叠新增计量。
   不再累加 Provider 的累计样本，不移除缓存来规避冻结 token budget。
   `estimated_usd`、`provider_reported_usd`、`billed_usd` 分别解释；null 表示证据不足，
   不把订阅、缺少价目或尚未对账称为免费。
3. 对照 Task Map、实际反馈距离、已完成产物和返工原因，区分需求工作量、
   重复读取、协议修复及未知遥测。历史耗时和 token 不能自动外推剩余成本。
   建议缩短上下文时列明仍须读取的来源、范围和独立验收，不删除原始 AC。
4. 在既有 Plan/Critic 评审工件中记录当前配置、建议差异、理由、证据和不确定性。
   可以建议保持配置。需要改变 lanes、预算、权限或拓扑时，申请现有受控配置修订，
   重新绑定和批准；禁止直接改运行中 YAML 或将旧评审移用于新代际。

验证：重放同一 usage 样本不增加账本；累计快照只贡献新增值；未知价格仍为 null；
CLI 与 Critic 正式输入可读同一事实。事实报告本身不证明语义建议合理或产品已交付。
