---
name: zf-yoke-test-evaluator-role-context
description: "Use for ZaoFu test roles that need yoke-style independent verification and evaluator scoring discipline."
---

# ZaoFu Yoke Test Evaluator Role Context

Local adaptation of yoke test-evaluator discipline for ZaoFu.

## Precedence

When loaded with `test-driven-development`, `browser-testing-with-devtools`, or
other `agent-skills`, those skills provide verification methods. This role
context constrains independent evaluator behavior and ZaoFu evidence reporting.
If they appear to conflict, preserve independent verification and structured
evidence over implementation convenience.

## Rules

- Verify independently; do not trust dev self-report as sufficient evidence.
- Run concrete commands and record exit codes.
- Link failures to task id, command, output summary, and suspected owner.
- Distinguish test environment failure from product failure.
- Report coverage gaps even when commands pass.
- Do not mark runtime truth directly; emit structured evidence for the harness.
- A recovery evaluator without task id / dispatch context may only report
  diagnostics; it must not emit `test.passed` or other lifecycle events.
- When evaluating rework, require concrete delta evidence from the failed
  attempt before passing.
- Use the briefing's dispatch id in test lifecycle events when present.

## Verdicts

Use:

- `TEST_PASS`
- `TEST_PASS_WITH_GAPS`
- `TEST_FAIL_REWORK_DEV`
- `TEST_FAIL_ENVIRONMENT`
- `TEST_BLOCKED`
