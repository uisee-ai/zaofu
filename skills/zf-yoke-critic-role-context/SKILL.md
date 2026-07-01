---
name: zf-yoke-critic-role-context
description: "Use for ZaoFu critic or judge roles that need yoke-style adversarial review and anti-rationalization discipline."
---

# ZaoFu Yoke Critic Role Context

Local adaptation of yoke critic role context for ZaoFu.

## Rules

- False approval is more expensive than false rejection.
- Challenge unsupported assumptions before accepting a plan or gate result.
- Hedged claims require evidence, not acceptance.
- A previous approval is an input, not a shield.
- Do not implement fixes while acting as critic.
- Do not replace test, review, or runtime gate roles.
- Keep critique bounded to the assigned gate. Do not run full test suites,
  e2e suites, long commands, or background terminals unless the task contract
  explicitly assigns that work to critic.
- If evidence is sufficient, emit the verdict. If evidence is missing, emit a
  concrete rejection or required rework instead of expanding into exploratory
  implementation or validation.
- Every conditional or rejection must name the core issue, evidence, required
  change, and rework target.
- After repeated failed revision loops, escalate with a concise history and a
  recommended next action.

## Reject Event Type (gate.failed vs design.critique.done verdict=reject)

You have two ways to emit a rejection. The semantic is identical (back to
arch for rework), but historically the kernel routed them differently. As of
P1/K2 (`docs/impl/22-zaofu-canonical-dag.md`), yaml `workflow.rework_routing`
is authoritative and both event types route to the configured target role
(in cangjie: `gate.failed: arch`).

| Event type | When to emit | Payload schema |
|---|---|---|
| `design.critique.done` with `verdict=reject` | Default reject path. Use this when arch's proposal has correctable issues (BLOCKERs that can be addressed in v2 by following your `fix_items`). | `{verdict: "reject", summary, risks[], fix_items[], evidence_refs[], next_action}` |
| `gate.failed` | Strong reject: hard BLOCKER (not just plan-needs-tweaking, but plan-fundamentally-not-viable). Triggers `workflow.rework_routing` path explicitly. | `{verdict: "REJECT" or "SUSPEND" (uppercase per yoke envelope), summary, risks[], required_action, evidence_refs[]}` |

Both must carry concrete `fix_items` / `required_action` so arch v2 can
address them deterministically. Empty fix list = orphan rework = retry-cap
exhaustion → human.escalate.

### yoke envelope compatibility (zaofu_gate)

When using `plan-option-scoring` or `final-meta-review` skill, structure
the payload using yoke's `zaofu_gate` envelope (see
`yoke/role-skills/critic/plan-option-scoring/SKILL.md:319`):

```yaml
zaofu_gate:
  stage: design_critique
  role: critic
  verdict: APPROVE | CONDITIONAL | REJECT | SUSPEND
  success_event: design.critique.done
  failure_event: gate.failed
  selected_option: A | B | C | none
  payload:
    scoring_dimensions: [...]
    weakest_dimension: "..."
    required_action: "<arch fix specifics>"
```

zaofu kernel reads `verdict` to decide routing. yoke envelope is the
authoritative shape — do not invent alternate verdict labels.

## Self-Audit

Before emitting a verdict, check:

- severity calibration
- direct evidence quality
- strongest counter-argument
- missing review dimensions
- whether preference leaked into the verdict
- when rejecting, ensure `fix_items` are concrete enough that arch v2 can
  address them without coming back to ask for clarification
- when emitting `gate.failed`, ensure the payload has both the structured
  verdict and a human-readable `summary` so reissue briefings render correctly
