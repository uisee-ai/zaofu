---
name: zf-verify-gap-producer-contract
description: "Use in ZaoFu issue, PRD, or refactor workflows when verify, rescan, judge, or post-verify agents must turn discovered product/parity/regression gaps into canonical gap artifacts and flow.gap_plan.ready or goal.gap_plan.ready events so the same run can loop back to implementation instead of starting a new manual round. Loop-back is fingerprint-deduped and commit-delta-gated by the kernel; see Expected Kernel Responses."
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

## Kernel Pairing (Verify Report → Gap Plan)

Gap production is downstream of the per-child verify report contract, not a
fresh prose exercise:

- `gap_tasks` must derive from the graded `gap_findings` rows of a schema-valid
  verify report — one whose `requirement_coverage_matrix` has at least one row
  (the `non_empty` schema tier) and which already passed the
  `verify.child.completed` / `verify.child.failed` event-schema validation.
  Do not mint gaps from impressions that never entered the report.
- Only Critical/must-fix findings belong in `gap_findings`; nits and
  suggestions do not trigger rework. Grading rules and the report contract
  itself are owned upstream by `yoke/verify-review`.
- When the project's effective event schema declares these report fields
  (via a schema profile or the `workflow.dag.event_schemas` override), the
  kernel auto-educates worker briefings with `requirement_coverage_matrix` and
  `gap_findings` placeholder examples. Fill the placeholders; never delete
  them. The default schema profiles require only the structural ids
  (`fanout_id` / `child_id` / `status`) plus `summary` / `evidence_refs` /
  `git_refs` — the matrix/findings tiers are project-level declarations,
  so check the project's schema before assuming enforcement.

## Required Outputs

When gaps exist, produce:

1. A gap report artifact with:
   - `schema_version`
   - `pdd_id` or `feature_id`
   - `trace_id`
   - `task_map_ref` or `base_task_map_ref`
   - `gap_tasks`
   - `evidence_refs`
   - `defer_or_out_of_scope` decisions for non-blocking gaps — skill-owned
     field (no kernel validator reads it); it exists for human/judge audit of
     what was consciously not turned into a gap task
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

## Expected Kernel Responses (Suppression Gates)

Two kernel gates bound the loop-back; hitting them is expected behavior, not
an error to retry around:

- `plan.minting.suppressed` (reason `pending_plan_same_fingerprint`):
  re-emitting an equivalent gap plan — same stage + pdd + task-set semantic
  fingerprint — while a plan with that fingerprint is still pending approval
  is deduped; no new approval request is minted (the payload carries
  `plan_fingerprint` and `duplicate_of`). Do not loop re-emitting the same
  plan; wait for the pending decision or change the task set.
- `fanout.retrigger.suppressed` (reason `no_delta_since_failure`): loop-back
  re-verify against the same `target_commit` that already failed is refused.
  A re-verify only proceeds after a new commit delta exists — the producer
  should attach the new delta/commit in `evidence_refs` when requesting
  re-verification. Dispatched children carry the pinned `target_commit` in
  their payload, so evidence must bind to that commit.

## No-Gap Output

When no blocking gap exists, produce a closure report with:

- source artifacts checked
- acceptance/test matrix coverage
- real E2E evidence refs when required
- explicit P1/P2 defer decisions
- final `flow.goal.closed` or equivalent terminal evidence. `judge.passed`
  alone is not terminal: when the workflow flow metadata (raw zf.yaml key
  `workflow._flow_metadata`, typically injected by the flow preset) declares
  a `quality_floor` with evidence ref groups (`quality_floor_ref_groups`),
  a `judge.passed` missing any configured group is blocked and the kernel
  emits `flow.goal.blocked`
  with `missing_ref_groups` instead — listing `flow.gap_plan.ready` /
  `goal.gap_plan.ready` / `flow.goal.closed` as its expected downstream
  events. The no-gap closure report must therefore include the configured
  quality-floor refs.

## Quality Rules

- Prose-only gap findings are mechanically rejected, not merely discouraged:
  when the project's event schema declares required/`non_empty` tiers for
  `verify.child.completed` / `verify.child.failed` reports, a missing or
  empty `requirement_coverage_matrix` or ungraded findings fail schema
  validation before any gap plan can be built on them.
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

## Related Skills

- `zf-verify-rescan-replan` — owns the rescan report, gate decision, and
  re-entry loop; this skill owns the gap event-payload contract. Both are
  separately wired in verify bundles, so keep them split and consistent.
- `yoke/verify-review` — owns the upstream per-child verify report contract
  (`requirement_coverage_matrix`, `gap_findings` grading,
  `verify.child.completed` schema) that this skill consumes as raw material.
- `zf-gap-task-synth` / `zf-goal-closure-replan-contract` — gap-task shaping
  and the goal-closure replan loop; they restate the gap-task quality rules
  above in their own stages.
