---
name: zf-harness-gate-evaluator
description: "Use when evaluating ZaoFu review, test, critic, quality, or judge gates with structured pass/fail evidence."
---

# ZaoFu Harness Gate Evaluator

This skill standardizes gate reports. It does not make the deterministic gate
decision by itself.

## Gate Report

Every gate report must include:

- gate name
- task id or feature id
- input artifact references
- checks performed
- pass/fail verdict
- blocker severity
- evidence paths or command outputs
- rework target when failed
- minimum condition required for pass

## Evaluation Rules

- Prefer false rejection over unsupported approval.
- Do not accept hedged claims such as "should work" without evidence.
- A prior approval is input evidence, not a shield.
- If the weakest required dimension fails, the gate fails.
- Route failures to the earliest role that can fix the root cause.

## Verdicts

Use one of:

- `PASS`
- `PASS_WITH_RISK`
- `FAIL_REWORK_DEV`
- `FAIL_REWORK_ARCH`
- `FAIL_NEEDS_OPERATOR`
