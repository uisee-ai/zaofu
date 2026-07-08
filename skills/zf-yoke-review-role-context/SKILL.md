---
name: zf-yoke-review-role-context
description: "Use for ZaoFu review roles that need yoke-style scoped, evidence-grounded code review."
---

# ZaoFu Yoke Review Role Context

Local adaptation of yoke review discipline for ZaoFu.

## Precedence

The five-axis review method, the severity grading table, and the structured
report shape all come from `yoke/verify-review`. This role context owns only
the **role boundary** and does not restate that method: load
`yoke/verify-review` for the method and report contract, and this context then
constrains what the ZaoFu review role may and may not do. If an upstream method
skill offers autofix or implementation follow-through, do not use that path
unless the task explicitly assigns mutation to this role; otherwise route fixes
back to dev.

## Rules

- Review changed behavior, risk, regressions, and missing tests first.
- Findings must carry severity, evidence, impact, and required fix. Severity is
  not cosmetic: only **Critical / must-fix** findings enter `gap_findings` and
  drive rework routing; a Nit or FYI that leaks into `gap_findings` burns a
  whole wasted rework round. Use the grading table in `yoke/verify-review`
  (发现分级) as the single source — do not maintain a second copy here.
- Do not redo implementation while acting as reviewer.
- Do not accept vague "looks good" or "probably works" claims.
- Keep review scoped to assigned diff and task contract.
- Git review checklist — audit-object binding comes **first**: when the
  briefing carries a `target_commit` (reader child dispatch pins it into the
  child payload, FIX-9), run `git rev-parse HEAD` and compare. If they differ,
  report a workdir mismatch (kernel event `fanout.child.workdir_mismatch`) and
  stop — do not issue a verdict on the wrong tree. Only then use the briefing's
  Git Evidence Context as the review boundary: base/head, `git log base..HEAD`,
  files touched, dirty files, and diff stat, verifying suspicious gaps with
  `git diff --stat <base>` and `git status --short`.
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
