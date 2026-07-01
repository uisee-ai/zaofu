---
name: zf-harness-eval-harness
description: "Use when designing or running an independent ZaoFu evaluator pass for product behavior, regressions, or user-flow evidence."
---

# ZaoFu Harness Eval Harness

This skill adapts yoke eval-harness discipline for ZaoFu test and judge roles.
It turns acceptance criteria into an independent evaluator pass.

## Eval Plan

Define:

- scenario id or task id
- user-visible behavior under evaluation
- setup commands or fixtures
- execution commands or browser/user-flow steps
- verification tier covered by each passing check
- expected observable result
- failure classification: product, test, environment, or harness

## Evaluation Rules

- Evaluate behavior from outside the implementation when possible.
- Prefer browser/API/CLI user-flow checks over static inspection alone.
- Keep the evaluator independent from the dev self-report.
- Record enough evidence for later replay or regression.
- If a check is flaky, report it as risk instead of silently passing.

## Output

Return:

- `eval_id`
- `task_id`
- `checks`
- `verdict`
- `evidence`
- `artifact_refs`
- `evidence_refs`
- `coverage_gaps`
- `rework_target`
