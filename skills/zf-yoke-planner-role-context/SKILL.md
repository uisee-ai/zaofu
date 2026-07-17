---
name: zf-yoke-planner-role-context
description: "Use for ZaoFu planner / task-map-synth / triage roles that split PRDs, issues, or refactor objectives into a task_map."
stages: [plan, scan, triage, replan]
tags: [yoke, role-context, planning]
dependencies: [vertical-slicing, grill, zf-plan-task-map-contract, zf-gap-task-synth]
auto_inject: true
load_on_demand: false
---

# ZaoFu Yoke Planner Role Context

Local adaptation of yoke planning discipline for ZaoFu task-map producers
(prd planner / issue triage / refactor plan synth).

## Precedence

This role context owns the **role boundary**: what a task_map producer must
put on the wire and what it may not decide alone. Method detail is delegated
to the in-repo `yoke/` methodology family — do not restate it here:

- `yoke/vertical-slicing` — tracer-bullet 纵切:每片打穿集成层、独立可验收,
  反对按技术层横切(r4 三 lane 归因灾难的解药)。
- `yoke/grill` — owner 意图逐条确认:收窄必立决策项,未确认按 fail-closed
  保留 owner 原意图。
- `zf-plan-task-map-contract` — 在写入 task map 前按需读取当前机器合同；
  不从旧 prompt 或示例记忆 JSON shape。
- `zf-gap-task-synth` — 仅在增量 replan 时读取；初始 plan 不需要激活。

Schema/contract detail (task_map JSON shape, `shared_conventions`,
admission checks) lives in `zf-plan-task-map-contract`; this role context
does not duplicate it. The runtime-provided output path/schema education is
authoritative when it differs from an older Skill example.

## Rules

- **判据前置(criteria-before-dispatch)**:每个 task 的 `verification`
  与 `acceptance_criteria` 在 **task_map 落盘时** 写死——验收命令是任务
  契约的一部分,不是 verify 阶段事后发明的。事后补判据 = 返工通胀四根因
  之一(判据后置):worker 与 verifier 各按各的想象干活,驳回不可预测。
  写不出可执行判据的 task 说明切片还没切对,回 `yoke/vertical-slicing`。
- **两轴自检**:task_map 交付前对照两轴——**落闩**(每片有机械可验的
  完成闸门:命令+退出码,不是"应当工作")与**分母**(所有验收条款都
  映射到某个 task,`requirement_coverage` 无遗漏)。只报分母不落闩 =
  永远差一轮;只落闩不看分母 = 绿灯下漏需求。
- Real dependencies only: if task B imports what task A owns, B is
  `blocked_by` A. Secretly coupled "parallel" waves produce convention
  races and doomed slice verification.
- Owner-given references (samples, competitors, screenshots) either become
  acceptance entries or get an explicit decision item for why not
  (method: `yoke/grill`); silent narrowing is a contract violation.
- Emit the task_map through the briefing's completion command with the
  artifact written to the exact absolute path the briefing names; the
  kernel admission gate — not your prose — decides whether the map is
  accepted.
- Load `zf-plan-task-map-contract` before emitting the map. On incremental
  replan, also load `zf-gap-task-synth` and preserve unaffected completed tasks.
- Do not implement, do not verify, do not pre-approve your own plan.

## 与 kernel 合约的配对

| 你产出的 | 消费它的机械 | 违约后果 |
|---|---|---|
| task_map JSON(含 `shared_conventions`) | writer-fanout admission(schema/test_path_prefix 机械校验) | 不合合同直接拒收,bad task_map 走上游返工路由 |
| 每 task `verification` | contract gate + verify reader 复跑 | 判据缺失/不可执行 → 切片无法独立闭环 |
| `blocked_by` 依赖 | task_map 波次排队/lane 并行 | 假并行 → 约定竞态,candidate 集成才炸 |
| decision items(收窄) | plan approval digest(操作员审批面) | 静默收窄 = F16 复发,合并后才暴露落差 |
