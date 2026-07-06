---
name: zf-verify-gap-producer-contract
description: "Use in ZaoFu issue, PRD, or refactor workflows when verify, rescan, judge, or post-verify agents must turn discovered product/parity/regression gaps into canonical gap artifacts and flow.gap_plan.ready or goal.gap_plan.ready events so the same run can loop back to implementation instead of starting a new manual round."
---

# ZaoFu Verify Gap Producer Contract

Use this skill in verify/rescan/judge roles when a workflow discovers that the
current implementation is incomplete.

## Boundary

- Let agents/adapter skills decide whether a project-specific behavior is a
  real gap.
- Let runtime consume canonical gap events and amend task maps.
- Do not encode project modules, UI details, provider names, or parity rules in
  runtime.

## Required Outputs

When gaps exist, produce:

1. A gap report artifact with:
   - `schema_version`
   - `pdd_id` or `feature_id`
   - `trace_id`
   - `task_map_ref` or `base_task_map_ref`
   - `gap_tasks`
   - `evidence_refs`
   - `defer_or_out_of_scope` decisions for non-blocking gaps
2. A canonical event request:
   - `flow.gap_plan.ready` for PRD/refactor/product flow gaps
   - `goal.gap_plan.ready` for goal-level closure gaps
3. Each gap task must include:
   - stable `task_id`
   - title and scope
   - owner role/lane or affinity tag
   - allowed paths or target module refs when known
   - acceptance criteria
   - verification command or evidence requirement

## No-Gap Output

When no blocking gap exists, produce a closure report with:

- source artifacts checked
- acceptance/test matrix coverage
- real E2E evidence refs when required
- explicit P1/P2 defer decisions
- final `flow.goal.closed`, `judge.passed`, or equivalent terminal evidence

## Quality Rules

- Do not output prose-only gap findings when the workflow can continue.
- Do not start a new run just because verify found gaps.
- Do not emit `*.passed` if open P0 gaps remain.
- Keep gap tasks incremental; do not regenerate the whole plan unless the base
  task map is invalid.
- Prefer linking to existing evidence over copying long transcripts.

## Event Payload Minimum

For `flow.gap_plan.ready` / `goal.gap_plan.ready`, include:

```json
{
  "pdd_id": "PDD-or-feature-id",
  "trace_id": "run-or-flow-trace",
  "task_map_ref": "artifacts/.../task-map.json",
  "gap_plan_ref": "artifacts/.../gap-plan.json",
  "gap_task_count": 1,
  "source": "verify-gap-producer"
}
```

The orchestrator bridge owns deterministic task-map amendment after this event.
