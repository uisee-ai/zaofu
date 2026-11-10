# Workflow 配置应用后已有 consumer 未重载

> 状态: active

优先级 P1。UI 在已有 runtime 上将 plan_approval false 改为 true，Apply/Start 显示已提交，
但 Kernel 使用旧配置并拒绝 consumer_config_stale。保护本身正确，缺少重载或用户可操作的启动前衔接。

复现证据：/tmp/zf-prd-ui-fake-kanban-20261110-103043/evidence/diagnostic-project.tar.gz 内 project/.ui-state/events.jsonl。
调用链：workflow config apply → apply_flow_submit → ensure_project_runtime 复用旧 PID →
approved_config_consumer_error 拒绝。不能直接删除校验或覆盖在途 Run 的执行策略。

- 设计受控配置生效/重载路径 → verify: 仅新 Run 使用已审批快照，已有 Run 身份与策略不被替换。
- UI 在启动前展示可恢复阻塞或完成明确的受控重载 → verify: 停止/重启保留审批与影响说明。
- 重放与并发启动 → verify: 不重复派发，不误杀其他项目，旧 consumer 仍 fail-closed。

真实 Codex 进一步复现：预先启用 plan_approval 仍不足以避免拒绝。
`/tmp/zf-prd-ui-real-kanban-20261110-110158/evidence/diagnostic-project.tar.gz` 中
`evt-3e00c84a4f46` 拒绝 `TASK-7CFAB7` 的 invoke。真实 Synthesis 调整 verify width、
质量命令，模板还恢复 resident Run Manager / Autoresearch 默认值；已有 consumer 未加载。
需要同时核查编译配置是否正确保留用户的 resident 设置，不能只修审批开关重载。

## 本轮实施

实现与交互分析：[PRD UI 恢复](../../docs/impl/prd-ui-recovery.md)。
验证与保留失败：[验证记录](../../docs/records/2026-11-10-prd-ui-recovery-validation.md)。
