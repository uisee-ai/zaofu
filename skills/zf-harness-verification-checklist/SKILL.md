---
name: zf-harness-verification-checklist
description: "Use before emitting ZaoFu review.approved, verify.passed, test.passed, or judge.passed; requires explicit verification coverage and fail-closed missing checks."
---

# ZaoFu Harness Verification Checklist

This skill adapts yoke verification-checklist behavior to ZaoFu. It is a
checklist for gate roles; the deterministic runtime still decides terminal
state.

## Checklist

Before passing a gate, verify:

- the task contract has behavior and verification criteria
- acceptance criteria are behavior-specific, not just `exit_code=0` or
  "tests pass"
- every declared `verification_tiers` item has passing evidence: `static`, `runtime`,
  `e2e`, and/or `manual_evidence`
- required `quality_gates.required_checks` have passed or failure is reported
- review/test/judge predecessor evidence is present when the topology requires it
- changed files or artifacts match the task scope
- skipped checks have an explicit reason and owner
- environment failure is separated from product failure

## Fail-Closed Conditions

Return a failing verdict when:

- required command evidence is absent
- a declared verification tier has no evidence
- a required command exits nonzero
- predecessor evidence is missing
- the task id cannot be linked to the evidence
- the result depends only on self-report
- the task contract is generic enough that passing the command would not prove
  the requested behavior

## Runtime Boundary

Emit structured evidence in the role output or completion event payload.
Do not directly move tasks to `done`; ZaoFu runtime emits
`task.done.evidence` or `task.done.blocked`.

For `review.approved`, `verify.passed`, `test.passed`, and especially
`judge.passed`, include machine-routable evidence with:

- `summary`
- `checks[]` with `command`, `exit_code` or `passed`, and `tier`
- `scores` for `correctness`, `completeness`, `regression_risk`, and
  `evidence_quality`
- `artifact_refs`
- `evidence_refs`
