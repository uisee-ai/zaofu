---
name: zf-yoke-planner-role-context
description: "Use for ZaoFu planner / task-map-synth / triage roles that split PRDs, issues, or refactor objectives into a task_map."
stages: [plan, scan, triage, replan]
tags: [yoke, role-context, planning]
dependencies: [vertical-slicing, grill, zf-plan-task-map-contract, zf-gap-task-synth, zf-project-adapter-matrix-enrichment, zf-plan-execution-readiness]
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
- `zf-project-adapter-matrix-enrichment` — 当 briefing 提供 portable matrix
  drafts 时，在最终提交前完成跨矩阵 id 闭包检查。
- `zf-gap-task-synth` — 仅在增量 replan 时读取；初始 plan 不需要激活。

Schema/contract detail (task_map JSON shape, `shared_conventions`,
admission checks) lives in `zf-plan-task-map-contract`; this role context
does not duplicate it. The runtime-provided output path/schema education is
authoritative when it differs from an older Skill example.

## Rules

- 在最终提交前加载 `zf-plan-execution-readiness`，记录条款级验收责任、切片可演示结果、
  阶段集成和条件决定的审查证据；机械数量检查不替代语义评估。

- **判据前置(criteria-before-dispatch)**:每个 task 的 `verification`
  与 `acceptance_criteria` 在 **task_map 落盘时** 写死——验收命令是任务
  契约的一部分,不是 verify 阶段事后发明的。事后补判据 = 返工通胀四根因
  之一(判据后置):worker 与 verifier 各按各的想象干活,驳回不可预测。
  写不出可执行判据的 task 说明切片还没切对,回 `yoke/vertical-slicing`。
- **AC 不是命令**:先把 owner/Goal 的可观察结果拆成稳定 `acceptance_id`,
  再把每个 mandatory AC 映射到一个或多个 command id。不要把“运行 pytest”
  写成 AC,也不要用 `exit_code=0` 代替产品行为。结构化 AC 使用
  `id`、`statement`、`mandatory`、`verification_owner`、
  `verification_tier`、`verification_command_ids`。
- **命令保持独立身份**:新 task map 把命令写入 `validation.commands[]`,
  每项至少包含 `id`、`command`、`acceptance_ids`、`owner`、`tier`,并按
  实际性质声明 `deterministic`、`reusable`、`timeout_seconds`。不要把多条
  命令拼成 `cmd1 && cmd2`;`verification` 单字符串只用于旧合同兼容。命令
  identity 包含完整字符串:必须按最终序列化后的原文可执行,不能用补转义后的
  “等价命令”代替。含多层引号/反斜杠的断言应落到本 task 拥有的测试或脚本。
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
- A blocking choice that only the owner can make is not an ordinary rework
  item. Preserve it as `owner_decision_items[]` in the semantic Plan result.
  Each item has a stable `decision_id`, one concrete `question`, explicit
  `options[]` (`option_id` + `label`), `blocking: true`, and `evidence_refs`.
  Do not guess the answer, hide it in prose, or repeatedly resubmit the same
  unresolved Plan. The runtime will bind the controlled owner response into a
  later Plan revision.
- Emit the task map through the briefing's completion command after writing it
  to the exact **workdir-relative** output path named by the briefing. Never
  write the configured state dir or root project directly; the Kernel
  relocates admitted workdir refs into canonical artifact storage and computes
  their digests. The Kernel admission gate, not your prose, decides whether the
  map is accepted.
- Load `zf-plan-task-map-contract` before emitting the map. On incremental
  replan, also load `zf-gap-task-synth` and preserve unaffected completed tasks.
- When Controlled Artifact Inputs include portable matrix drafts, load
  `zf-project-adapter-matrix-enrichment` before emitting and run its Mechanical
  Closure Check against the final serialized `plan_ports`, not an earlier draft.
- **计划态不冒充执行态**:mandatory AC 的截图、trace、receipt 或其他运行证据在
  implementation 前尚不存在，不是 plan blocker。Planner 要证明的是 producer、
  allowed paths、真实命令和 runner 方法可满足；其中 browser/E2E 场景按依赖技能
  `zf-browser-e2e-contract` 声明 Docker Playwright。只有 runner 本身不可用/被禁止且
  无 sanctioned equivalent 时才标 environment blocked，不得把“未来证据未生成”
  循环回 plan rework。
- Do not implement, do not verify, do not pre-approve your own plan.

## 与 kernel 合约的配对

| 你产出的 | 消费它的机械 | 违约后果 |
|---|---|---|
| task_map JSON(含 `shared_conventions`) | writer-fanout admission(schema/test_path_prefix 机械校验) | 不合合同直接拒收,bad task_map 走上游返工路由 |
| 每 task `validation.commands[]` + AC mapping | contract snapshot + Impl/Verify/Candidate | identity 丢失会导致证据不可复用；语义充分性由 Critic/Verify 判断 |
| `blocked_by` 依赖 | task_map 波次排队/lane 并行 | 假并行 → 约定竞态,candidate 集成才炸 |
| typed `owner_decision_items[]`(收窄) | durable owner checkpoint + controlled response ref | 静默收窄或 prose-only 决策项 = F16 复发；未回答前不得猜测 |
