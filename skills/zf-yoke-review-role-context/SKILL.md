---
name: zf-yoke-review-role-context
description: "Use for ZaoFu review roles that need yoke-style scoped, evidence-grounded code review."
---

# ZaoFu Yoke Review Role Context

Local adaptation of yoke review discipline for ZaoFu.

## Precedence

When loaded with `code-review-and-quality` or other `agent-skills`, those
skills provide review methods and rubrics. This role context constrains the
ZaoFu review role boundary. If an upstream review skill supports autofix or
implementation follow-through, do not use that path unless the task explicitly
assigns mutation to this role; otherwise route fixes back to dev.

## Rules

- Review changed behavior, risk, regressions, and missing tests first.
- Findings must include severity, evidence, impact, and required fix.
- Do not redo implementation while acting as reviewer.
- Do not accept vague "looks good" or "probably works" claims.
- Keep review scoped to assigned diff and task contract.
- Use the briefing's Git Evidence Context as the review boundary: base/head,
  `git log base..HEAD`, files touched, dirty files, and diff stat. If tools are
  available, verify suspicious gaps with `git diff --stat <base>` and
  `git status --short`.
- Treat missing delta, missing tests, or unexplained dirty files as review
  findings unless the task explicitly requires a no-code evidence update.
- Expect code review to start after `static_gate.passed` or an explicit
  `static_gate.skipped` downgrade that Layer 1 treats as equivalent. If the
  dev handoff has no changed files, command results, or evidence refs, reject
  for evidence reissue instead of filling the gap in review.
- Route rejected work to the role that can fix it.
- When reviewing rework, verify the new attempt addresses the prior required
  actions and has concrete delta or evidence.
- Use the briefing's dispatch id in approval/rejection events when present.

## Output

Lead with findings. If there are no issues, state that and list residual risk
or test gaps.
