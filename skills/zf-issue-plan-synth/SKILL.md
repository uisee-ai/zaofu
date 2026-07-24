---
name: zf-issue-plan-synth
description: "Use for ZaoFu issue workflows when triage and planning must produce a dispatchable issue repair task_map. Pair with zf-plan-task-map-contract; domain skills may be added after this skill."
---

# ZaoFu Issue Plan Synthesis

## Goal

Turn a bug/issue request plus triage evidence into a small repair plan and a
dispatchable `task_map.json`. The plan must let implementation workers start
without rereading the entire conversation.

## Inputs To Preserve

- User issue statement and reproduction facts.
- Triage findings, suspected root cause, and unknowns.
- Files, commands, logs, or UI states used as evidence.
- Any scope boundaries or blocked paths from `zf.yaml`.

## Required Outputs

Write:

- `issue-plan.md`: concise human plan with root-cause hypothesis, slices,
  verification, and risks.
- `task_map.json`: writer-fanout task map.
- `source_index.json`: evidence/source list.

Emit top-level `plan_artifact_ref`, `task_map_ref`, `source_index_ref`,
`artifact_refs`, and `evidence_refs`. Do not put these only inside `report`.
Also declare the logical inputs consumed by the task map through
`required_plan_ports`; use `issue_spec`, `goal_claim_set`, `task_map`, and
`planning_result` unless the issue profile narrows them. Runtime owns Package
construction/current selection, so do not emit Package lifecycle events.

## Source Provenance

Every issue task must preserve the issue evidence that justifies it. Add one
of these to each task:

- `source_key` / `source_keys` for issue, triage, log, or reproduction anchors.
- `source_ref` / `source_refs` for files, UI states, logs, or report sections.
- `source_excerpt` for the exact observed symptom or root-cause fact.

If the task body is compact, write `source_index.json` with `tasks[]` or
`task_sources[]` entries keyed by `task_id`. A global `sources[]` list is only
acceptable for a legacy one-task issue plan; use per-task anchors for all new
outputs.

## Task Design

Prefer one or two vertical fixes:

- `dev-core` for backend/runtime/kernel/CLI changes.
- `dev-web` for dashboard/browser-visible changes.

Each task needs `allowed_paths`, expected behavior, verification command, and
handoff evidence for verify agents. Avoid duplicate ownership of the same
exclusive file unless the tasks are serialized by dependency.

## Completion Check

Before emitting success:

1. `task_map.json` is valid JSON and contains non-empty `tasks`.
2. Every task has `task_id`, owner/affinity, `allowed_paths`, acceptance, and
   verification.
3. Every task has direct source anchors or is mapped by `source_index_ref`.
4. The success payload has top-level `task_map_ref`.

## Goal Closure Loop

Issue plans are allowed to evolve after verify. If later evidence shows the
issue is still not fixed, use:

- `zf-verify-rescan-replan` to compare the implemented behavior with the
  original issue and reproduction evidence.
- `zf-goal-closure-replan-contract` with `goal_kind: "issue"` and
  `gap_category: "issue_gap"` to produce `goal-gap-plan.v1`.
- `zf-gap-task-synth` to append only the missing repair work through
  `task_map.amended` / `task_map.ready` with `resume_scope:
  "gap_tasks_only"`.

Do not ask workers to restart the full issue plan when a bounded gap task can
close the remaining behavior.
