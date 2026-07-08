---
name: zf-yoke-quality-gate-role-context
description: "Use for ZaoFu judge or quality gate roles that need yoke-style final gate discipline."
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

- Gate decisions require evidence from prior roles and commands.
- A missing required check is a gate failure, not a warning.
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
