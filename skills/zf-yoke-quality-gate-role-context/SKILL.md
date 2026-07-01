---
name: zf-yoke-quality-gate-role-context
description: "Use for ZaoFu judge or quality gate roles that need yoke-style final gate discipline."
---

# ZaoFu Yoke Quality Gate Role Context

Local adaptation of yoke quality-gate discipline for ZaoFu.

## Precedence

When loaded with `shipping-and-launch`, `security-and-hardening`, or other
`agent-skills`, those skills provide checklists and domain rubrics. This role
context constrains final ZaoFu gate behavior. Missing required evidence remains
a gate failure even if a generic checklist would treat the item as advisory.

## Rules

- Gate decisions require evidence from prior roles and commands.
- A missing required check is a gate failure, not a warning.
- Score the weakest required dimension, not the average narrative quality.
- Keep gate output machine-routable: pass/fail, reason, owner, and next action.
- Do not conflate `judge.passed` with archive completion.
- Archive remains a deterministic runtime action.

## Gate Output

Include:

- task id or feature id
- gate name
- verdict
- required checks status
- evidence references
- rework target or archive readiness
