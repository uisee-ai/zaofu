---
name: zf-gap-task-synth
description: "Use to synthesize bounded gap tasks from a failed verify/rescan result without duplicating the original task-map schema or reopening unrelated completed work."
---

# ZaoFu Gap Task Synthesis

## Task Shape

Each generated gap task must be a normal task-map task with:

- stable `task_id`;
- `owner_role` and `affinity_tag`;
- `parent_task_id` when it patches a previous task;
- `claim_paths` / `allowed_paths`;
- explicit acceptance criteria;
- focused verification commands;
- direct `source_refs` to the failing report, source goal, or runtime evidence;
- `goal_kind`, `gap_category`, and `gap_kind`.

Do not synthesize vague tasks such as "finish web UI" without precise source
anchors and verification.

## Ownership

Keep gaps small and lane-friendly:

- reuse the original lane affinity when the same module owns the gap;
- use a new affinity only when the gap belongs to a different module;
- avoid two concurrent gap tasks owning the same exclusive root file;
- put root assembly/package files under an assembly/root task when needed.

## Evidence Contract

Add an `evidence_contract` or source fields that preserve:

- `goal_id`, `goal_kind`, `gap_category`, `gap_kind`;
- `parent_task_id` and `affinity_tag`;
- `source_refs`;
- `repro_ref` and `acceptance_id` when available;
- `replan_history_ref`;
- `affected_tasks` and `gate_changes` when the replan changed expectations.

The worker briefing must show this context before implementation.

## Emit Discipline

Gap task synthesis should produce artifacts first, then emit events. Do not
mark the goal done from the synth stage. Final closure belongs to verify/judge
after the amended tasks pass and the rescan report shows no open P0/P1 gaps.

Do not write directly to `events.jsonl`, `kanban.json`, `feature_list.json`,
`progress.md`, or `memory/`. Use artifacts plus the normal task-map amend event
path so Layer 1 remains the only runtime state writer.
