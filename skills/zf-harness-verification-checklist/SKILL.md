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

- the audit target is pinned first: when the dispatched child payload carries
  a `target_commit` (pin-commit), the workdir `HEAD` equals it before any
  review starts; on mismatch report a workdir mismatch and stop instead of
  continuing the review (the runtime's dispatch-side analog is
  `fanout.child.workdir_mismatch`, and the `fanout.retrigger.suppressed`
  convergence gate depends on this pinned commit)
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

## Fanout Verify Reader Report Contract

When acting as a fanout verify/judge reader (reporting via
`verify.child.completed` / `judge.child.completed`), the report must carry:

- `requirement_coverage_matrix` with at least one row; each row's
  `requirement_id` comes from a task contract or PRD acceptance clause (not
  invented), plus `source_ref`, `status`, `evidence_refs`, `gap_summary`, and
  `replan_action`
- `gap_findings` restricted to Critical / must-fix findings; nits and
  suggestions stay out of `gap_findings` and must not trigger rework
  (severity gating is a skill-owned convention — the kernel does not validate
  finding levels)
- `replan_recommendation` alongside any gap finding

Shipped schema profiles require only the baseline child-report fields. When
the project's `workflow.dag.event_schemas` enables the `non_empty` tier on
the event's nested `report` rule (e.g.
`non_empty: [requirement_coverage_matrix, evidence_refs]`), an empty matrix
is rejected at the schema gate. The runtime pairs the configured schema with
briefing education — schema-required fields are injected as placeholders
into the reader briefing — so a missing field in your report is a contract
violation, not a discovery problem.

## Scope Boundary

The full five-axis review method and the complete matrix / severity /
`target_commit` pairing contract live in the `yoke/verify-review` skill (its
in-repo landing may still be pending; until then this checklist stands
alone). This skill remains the fail-closed baseline checklist for gate
roles — keep method details in `yoke/verify-review` to avoid dual-source
drift. For the reject-side gap output contract, see
`zf-verify-gap-producer-contract`; for gate report standardization, evidence
collection, and done-declaration shape, see `zf-harness-gate-evaluator`,
`zf-harness-evidence-collection`, and `zf-harness-done-contract`.
