---
name: zf-prd-plan-synth
description: "Use for ZaoFu PRD workflows. Defines the PRD author -> critic -> task-map handoff and the dispatchable plan contract for product delivery."
---

# ZaoFu PRD Plan Synthesis

## Stage Contract

PRD delivery is sequential:

1. `prd-author` writes a durable PRD artifact and emits `prd.ready`.
2. `prd-critic` reviews that artifact and emits approval or blockage.
3. `task-map-synth` converts the approved PRD into `task_map.json`.

Do not run author and critic as peers when the critic needs the author's
artifact.

## PRD Author Output

Write `prd.md` before emitting `prd.ready`. The PRD must be a durable artifact
under the project root, normally `docs/prds/` or `docs/plans/`. Do not emit a
memory-only PRD or mention the path only in prose.

Emit top-level:

```json
{
  "prd_ref": "docs/plans/example-prd.md",
  "artifact_refs": ["docs/plans/example-prd.md"],
  "evidence_refs": ["<source or research refs>"]
}
```

`artifact_refs` must include `prd_ref`. `evidence_refs` must be non-empty and
should point to channel messages, research fanout outputs, issue links, user
requirements, or source facts used to write the PRD. These fields may also be
duplicated inside `report`, but downstream runtime and critic stages consume the
top-level payload.

The PRD should define users, problem, non-goals, acceptance criteria, release
risks, and verification expectations.

## Critic Output

Review `prd_ref`, `artifact_refs`, and `evidence_refs` from the trigger event
and emit a concise verdict. If the PRD is not reviewable, emit the configured
failure event with the missing artifact reason. Do not approve memory-only PRD
prose or a PRD event whose `artifact_refs` does not include `prd_ref`.

## Task-Map Synth Output

After PRD approval, write:

- `prd-plan.md`: human implementation plan.
- `task_map.json`: dispatchable vertical slices.
- `source_index.json`: PRD and evidence index.

Emit top-level `plan_artifact_ref`, `task_map_ref`, `source_index_ref`,
`artifact_refs`, and `evidence_refs`.

The generated `task_map.json` must follow `zf-plan-task-map-contract`: every
task needs direct `source_key` / `source_keys` / `source_ref` / `source_refs` /
`source_excerpt`, or `source_index.json` must map every `task_id` through
`tasks[]` or `task_sources[]`.

## Task Design

Split by behavior, not by component-only layers. A good PRD task map usually
has API/runtime/web slices, each with:

- owner role such as `dev-api`, `dev-runtime`, or `dev-web`;
- `allowed_paths` and `exclusive_files`;
- dependencies and wave;
- acceptance criteria tied to the PRD;
- verification commands for product, API, and web verify agents.

## Goal Closure Loop

PRD delivery can replan after verify without discarding completed slices. If
verify or judge finds unmet acceptance criteria, use:

- `zf-verify-rescan-replan` to rescan the implementation against the approved
  PRD and runtime evidence.
- `zf-goal-closure-replan-contract` with `goal_kind: "prd"` and
  `gap_category: "acceptance_gap"` to write a `goal-gap-plan.v1` artifact.
- `zf-gap-task-synth` to create bounded gap tasks with PRD acceptance ids,
  source refs, and focused verification.

The amended task map must dispatch only the new gap tasks with
`resume_scope: "gap_tasks_only"`; final closure still requires a clean rescan
with no open P0/P1 acceptance gaps.
