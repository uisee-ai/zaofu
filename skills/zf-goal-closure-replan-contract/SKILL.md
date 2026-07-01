---
name: zf-goal-closure-replan-contract
description: "Use when a ZaoFu issue, PRD, or refactor workflow must keep scanning, planning, implementing, and verifying until the stated goal is closed. Defines the generic goal-gap-plan and replan amendment contract."
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

## Required Gap Plan

Write a durable `goal-gap-plan.v1` JSON artifact:

```json
{
  "schema_version": "goal-gap-plan.v1",
  "goal_id": "<issue/prd/refactor id>",
  "goal_kind": "issue|prd|refactor|custom",
  "gap_category": "issue_gap|acceptance_gap|parity_gap|custom",
  "replan_history_ref": "docs/plans/<goal>/replan-history.jsonl",
  "gap_tasks": [
    {
      "task_id": "<stable new task id>",
      "title": "<specific missing behavior>",
      "parent_task_id": "<original task id, if any>",
      "affinity_tag": "<lane/module/topic>",
      "claim_paths": ["<owned paths>"],
      "acceptance": ["<observable closure criterion>"],
      "verify_commands": ["<focused verification command>"],
      "source_refs": ["<report/scan/issue/prd refs>"],
      "repro_ref": "<optional reproduction/evidence ref>",
      "acceptance_id": "<optional PRD/issue acceptance id>"
    }
  ]
}
```

Every gap task must include `claim_paths`, `acceptance`, `verify_commands`, and
`source_refs`. Empty generic TODO tasks are invalid.

## Runtime Path

Use the existing canonical bridge:

1. Emit or produce a gap-plan artifact.
2. Convert it into a full amended `task_map.json`.
3. Emit `task_map.amended` and `task_map.ready` with
   `resume_scope: "gap_tasks_only"`.
4. Dispatch only the new `gap_task_ids`; keep original finished tasks stable.

Do not create a second task schema or write directly to `events.jsonl`,
`kanban.json`, `feature_list.json`, `progress.md`, or `memory/`.

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
