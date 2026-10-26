# 187-01 · 需求对账与未决事项方法

> 状态: active
> 授权: 用户明确要求拆分 187 并按 backlog 实施；本项由 backlogs 移入 tasks/active。
> 来源: [设计 187](../../docs/design/187-bmad-inspired-epic-task-map-and-tdd.md) 187 §3.1–3.2。

## 范围

复用来源覆盖和决定工件；新增可复用方法并经 Channel synthesizer、Planner/Critic 依赖闭包接入。

## 验收

1. 加入对账与分层未决方法 → verify: 角色依赖物化到实际 worker，包含正文和引用；不新增状态/合同。
2. 对固定需求案例审查 → verify: 定性约束、冲突和延期均有明确来源与落点。

## 验证与评估记录

新增需求对账方法，经 Channel 直接依赖和 Planner/Critic 的 readiness 依赖物化。
`tests/test_plan_readiness_skills.py` 13 passed；来源样例 5 条及冲突变体人工复核见评估记录。
定性要求、延期验收与冲突来源均保留；未将人工复核冒充独立模型评审。

完整命令、失败修复与未覆盖范围见[评估记录](../../docs/records/2026-10-26-0336-187-delivery-validation.md)。
