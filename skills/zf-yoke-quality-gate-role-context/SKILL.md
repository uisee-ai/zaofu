---
name: zf-yoke-quality-gate-role-context
description: "Use for ZaoFu judge or quality gate roles that need yoke-style final gate discipline."
stages: [judge, verify]
tags: [yoke, role-context, quality-gate]
dependencies: [verify-review]
auto_inject: true
load_on_demand: false
---

# ZaoFu Yoke Quality Gate Role Context

Local adaptation of yoke quality-gate discipline for ZaoFu.

## Precedence

This role context owns the **role boundary** of the final ZaoFu gate: what
counts as required evidence, when a gate fails, and how a verdict is routed.
Gate *method* is delegated to the in-repo `yoke/` methodology family — do not
restate it here:

- `yoke/verify-review` — the per-child verify report contract, requirement
  coverage, and pin-commit (`target_commit`) binding that this gate consumes.

Missing required evidence remains a gate failure even if a generic checklist
would treat the item as advisory.

## Rules

- **受审对象是 candidate,不是 target**(A3;r3 light 终审误拒实锚,
  2026-07-06):评 `candidate_ref`@`candidate_head_commit`;briefing 未带
  candidate_ref 时按 `candidate/<pdd_id>` 前缀解析(`runtime.git.
  candidate_branch_prefix`)。`target_ref` 只是 ship 后的合流目的地——
  其未解析/为空/陈旧**不得**作为拒因(kernel 已在 reader briefing 注入
  同源 SUBJECT OF REVIEW 条款,skill 层与其一致)。
- Gate decisions require evidence from prior roles and commands.
- A missing required check is a gate failure, not a warning.
- **两轴判决(缺一即不完整)**:**落闩**——机械闸门逐个过(命令+退出码,
  required checks 全绿),不接受"应当工作";**分母**——覆盖矩阵对齐全部
  验收条款,无遗漏条目。只报分母不落闩 = 永远差一轮;只落闩不看分母 =
  绿灯下漏需求。判决书两轴分别给结论。
- Score the weakest required dimension, not the average narrative quality.
- Keep gate output machine-routable: a tri-state verdict (see below), reason,
  owner, and a delta-producing next action.
- Do not conflate `judge.passed` with archive completion.
- Archive remains a deterministic runtime action.

## Verdict vocab

**汇报约定,kernel 消费的是事件类型 + 字段** —— 门不校验 enum 字符串,而是发一个
事件,reactor 消费它的类型 + `payload.verdict`。每个 verdict 映射到内核真正消费的
东西:

| Report verdict | Kernel consumption (grep-verified) |
|---|---|
| pass | `judge.passed` 事件(terminal candidate → ship) |
| fail | `judge.failed` 事件 → `_on_gate_failed` 走返工路由 |
| SUSPEND | 门/judge 事件上的 `payload.verdict == "SUSPEND"`,或 `review.suspended` / `test.suspended` 事件 —— reactor ω-1.b(2026-05-18)经 `_on_suspended` 路由:task → blocked + `human.escalate`,**不烧 rework 配额**。用于外部依赖断/需更多信息,不是失败返工。 |
| DONE_WITH_CONCERNS / TEST_PASS_* | 无对应内核事件 —— **skill-owned 汇报约定(无内核校验)**;降级为真实的 `judge.passed` + 显式 concern 说明,或 `judge.failed`,不要暗示内核会校验这个枚举。 |

## Gate Output

Include:

- task id or feature id
- gate name
- verdict —— `pass` / `fail` / `SUSPEND`(见 Verdict vocab;SUSPEND 走升级
  而不烧 rework 配额)
- required checks status
- evidence references —— **必须指名 verify report 的 `requirement_coverage_matrix`
  为结构化输入**。内核 schema 可把它钉到 `non_empty` 档位
  (`src/zf/core/verification/event_schema.py`):**空矩阵 = 证据缺失 = 门失败**
  (r4 全轮 9 份空矩阵即反面锚)。
- rework target or archive readiness

## Ship readiness(发布就绪判定)

controller 预设下 `judge.passed` 不是"打个分"——kernel 会立即 auto-ship
(`runtime.git.auto_ship_on_judge_passed`)把 candidate 合入
`ship_target_branch`。因此:

- **判过 = 判定可发布**。两轴齐(落闩+分母)且合并树机械闸门
  (`quality_gates` / candidate gate)全绿才 pass;"代码大概没问题"
  不构成 pass。
- **回退语义不对称**:pass 之前拒绝 = candidate 不合流,零回滚成本;
  pass 之后反悔 = 目标分支上 `git revert`,真实成本。犹豫时用
  fail(给 delta-producing required_action)或 SUSPEND(外部依赖断),
  不发"有保留的 pass"。
- ship/archive 本身是 deterministic runtime 动作,不需要也不允许
  gate 角色手工 merge。

## Rejection discipline

FIX-15②③ 判审收敛门(`_delta_gate_allows` in
`src/zf/runtime/orchestrator_fanout.py`):

- 对**同一 pinned `target_commit`** 无 delta 的重复驳回会被内核抑制为
  `fanout.retrigger.suppressed` —— 重复驳回不产生新审计,只产生噪音。
- 同段连续 ≥3 次驳回不收敛自动升级:`human.escalate` reason
  `judge_nonconvergence` → Tier-2 `diagnosis.requested`
  (`src/zf/runtime/diagnosis.py`)。
- 因此**每次驳回必须给出能产生 delta 的 `required_action`**(worker 可执行的
  具体修复/证据;该字段由 `_rework_required_actions` 消费)。只重述同一投诉、
  针对未变 commit 的 required_action 会被抑制并升级诊断,而不会被重新判审。
