---
name: zf-goal-closure-replan-contract
description: "Use when a ZaoFu issue, PRD, or refactor workflow must keep scanning, planning, implementing, and verifying until the stated goal is closed. Defines the generic goal-gap-plan input sources and the bridge-event replan contract; the kernel owns the task_map amend and enforces the loop idempotency gates."
---

# ZaoFu Goal Closure Replan Contract

## Purpose

Use this skill after an initial task map exists and later verification,
rescan, review, or runtime evidence shows the goal is not complete. The goal is
not to rewrite the whole plan; it is to append precise gap work through the
canonical task-map path.

This applies to:

- issue repair: `goal_kind: "issue"`, `gap_category: "issue_gap"`;
- PRD delivery: `goal_kind: "prd"`, `gap_category: "acceptance_gap"`;
- refactor parity: `goal_kind: "refactor"`, `gap_category: "parity_gap"`;
- other project goals with an explicit `goal_kind` and `gap_category`.

## Gap Inputs (precedence)

Gaps enter the closure loop through two paths — prefer the first:

1. **Verify report `gap_findings` (primary).** A schema-valid per-child verify
   report whose graded `gap_findings` rows embed a `gap_task` feeds the rework
   loop with no separate artifact: the kernel's `_gap_tasks_from_payload`
   (candidate_rework.py) lifts `findings[].gap_task` / `findings[].gap_tasks`
   (and top-level `gap_tasks`) straight into gap work. The report must carry a
   non-empty `requirement_coverage_matrix` — the `non_empty` schema tier in
   `core/verification/event_schema.py` — so gaps derive from graded coverage,
   not impressions. Report grading/schema is owned upstream by
   `zf-verify-gap-producer-contract` / `yoke/verify-review`.
2. **`goal-gap-plan.v1` artifact (aggregation / manual path).** When gaps span
   several sources, or no per-child report carries them, aggregate into the
   durable gap-plan artifact below.

Both paths converge on the same bridge event and the same amended `task_map`.

## Required Gap Plan

For the aggregation path, write a durable `goal-gap-plan.v1` JSON artifact
(kernel-validated by `validate_goal_gap_plan_payload`):

```json
{
  "schema_version": "goal-gap-plan.v1",
  "goal_id": "<issue/prd/refactor id>",
  "goal_kind": "issue|prd|refactor|custom",
  "gap_category": "issue_gap|acceptance_gap|parity_gap|custom",
  "replan_history_ref": "docs/plans/<goal>/replan-history.jsonl",
  "gap_tasks": [
    { "task_id": "<stable id>", "title": "<missing behavior>", "...": "per-task fields owned by zf-gap-task-synth" }
  ]
}
```

The envelope fields (`schema_version` / `goal_id` / `goal_kind` /
`gap_category` / `replan_history_ref` / `gap_tasks`) are the kernel-validated
part. The per-`gap_task` field shape — `claim_paths`, `acceptance`,
`verify_commands`, `source_refs`, and the rest — is owned by `zf-gap-task-synth`;
do not re-maintain that checklist here. Empty generic TODO tasks are invalid on
either path.

## Runtime Path (kernel bridge owns the amend)

The agent's only job is to produce a schema-valid gap-plan and emit one bridge
event. The kernel does the amend + ready deterministically — do NOT hand-build
the amended task map:

1. Produce/persist the gap input (verify report `gap_findings`, or a
   `goal-gap-plan.v1` artifact — see Gap Inputs).
2. Emit exactly one bridge event — `gap_plan.ready`, `goal.gap_plan.ready`, or
   `flow.gap_plan.ready` — carrying `pdd_id`/`feature_id`, `trace_id`, and
   `task_map_ref` (plus `gap_plan_ref` when the plan is a separate artifact).
3. The orchestrator bridge `_bridge_gap_plan_ready_to_task_map` then writes the
   amended full `task_map.json` and re-emits `task_map.amended` +
   `task_map.ready` with `resume_scope: "gap_tasks_only"`, dispatching only the
   new `gap_task_ids` and keeping finished tasks stable.

Do NOT hand-emit `task_map.amended` / `task_map.ready` yourself, do not create a
second task schema, and do not write directly to `events.jsonl`, `kanban.json`,
`feature_list.json`, `progress.md`, or `memory/`. The canonical
`flow.gap_plan.ready` / `goal.gap_plan.ready` event-payload contract is owned by
`zf-verify-gap-producer-contract`.

## Loop Idempotency Gates

The loop keeps scanning, planning, implementing, and verifying until the goal
closes — but two kernel gates bound blind retries. Hitting them is expected
behavior, not an error to retry around:

- `plan.minting.suppressed`: re-emitting a gap plan with the same
  `plan_fingerprint` while an equivalent plan is still pending is deduped (the
  payload carries `plan_fingerprint` + `duplicate_of`). Do not re-emit the
  identical plan; wait for the pending decision or change the task set.
- `fanout.retrigger.suppressed` (reason `no_delta_since_failure`): a re-verify
  against the same `target_commit` that already failed is refused. Land a new
  commit delta and bind the gap evidence to it, or escalate — never spin the
  loop with no delta.

When suppressed, the correct move is to produce a delta or escalate, not to
retry the same replan. `zf-verify-gap-producer-contract` owns the full
suppression-gate payloads.

## Replan History

Append one JSONL row per replan decision to `replan_history_ref` with:

- source event or scan id;
- detected gap summary;
- accepted/rejected alternatives;
- generated `gap_task_ids`;
- affected original task ids;
- gate changes, if verification expectations changed.

Workers must receive this context through the task evidence contract so they
understand why the gap task exists.

## Related Skills

- `zf-gap-task-synth` — authoritative per-`gap_task` shape and evidence
  contract; this skill owns the `goal-gap-plan.v1` envelope and the loop, not
  the per-task field checklist.
- `zf-verify-gap-producer-contract` — authoritative `flow.gap_plan.ready` /
  `goal.gap_plan.ready` event-payload contract and the full suppression-gate
  payloads; also owns the verify-report `gap_findings` grading this loop
  consumes.
- `zf-verify-rescan-replan` — rescan report, gate decision, and re-entry loop.
