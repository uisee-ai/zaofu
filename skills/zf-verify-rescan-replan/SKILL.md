---
name: zf-verify-rescan-replan
description: "Use in verify or judge stages when completed work must be rescanned against the original goal and any remaining gaps must be converted into bounded replan work."
---

# ZaoFu Verify Rescan Replan

## When To Trigger

Trigger this skill when verify/judge evidence shows any of these:

- required P0/P1 behavior is missing or only partially implemented;
- implementation passed local tests but does not satisfy the original issue,
  PRD, or refactor parity goal;
- the produced UI/API/CLI is a stub or talks to the wrong backend;
- source comparison finds uncovered original capability, tool, provider,
  memory, context, gateway, or dashboard behavior.

## Required Rescan Output

The rescan must write a durable report with:

- `goal_id`, `goal_kind`, and `gap_category`;
- source artifacts compared, including original goal and produced code;
- inventory artifacts compared, when the workflow exposes `inventory_refs` or
  `source_inventory_ref`;
- capability/test rows with priority and status;
- `open_p0_p1_gap_count`;
- candidate `gap_tasks` with source refs and verification commands;
- `runtime_evidence_refs` when runtime behavior is relevant.

If `open_p0_p1_gap_count` is greater than zero, do not emit final pass. Produce
a `goal-gap-plan.v1` artifact and route it through task-map amend.

## Gate Behavior

Use artifact/matrix gate config to check:

- the rescan report exists;
- required inventory refs are mapped by acceptance/test/gap matrix rows when
  `inventory_coverage` is configured;
- required report fields are non-empty;
- open P0/P1 gaps are zero before final judge pass;
- gap task-map artifacts are valid before re-entering implementation.

The gate is evidence-based. It should block final closure for missing behavior,
but it should not restart unrelated finished tasks.

Do not mutate `events.jsonl`, `kanban.json`, `feature_list.json`, `progress.md`,
or `memory/` directly. Produce artifacts and emit the configured event intent;
the kernel owns state transitions and projections.

## Re-entry Rule

Re-entry must be bounded:

- dispatch only generated gap tasks;
- preserve lane affinity and parent task context;
- include `replan_history_ref` and affected task ids in worker briefings;
- after gap verify, rescan again until closed or explicitly blocked.
