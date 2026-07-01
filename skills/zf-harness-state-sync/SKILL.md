---
name: zf-harness-state-sync
description: "Use when a ZaoFu role reports progress, blockers, done intent, or state mismatch; keeps runtime truth in deterministic stores and events."
---

# ZaoFu Harness State Sync

This skill is a ZaoFu-local harness overlay. It guides agent reporting only; it
does not authorize direct writes to runtime truth files.

## Rules

- Treat `zf.yaml` as the control plane.
- Treat `project.state_dir` as runtime state, not source code.
- Do not edit `events.jsonl`, `kanban.json`, `session.yaml`,
  `feature_list.json`, or `role_sessions.yaml` by hand.
- Report intent through `zf` CLI actions or structured role output that the
  harness can convert into events.
- Use the current briefing's dispatch id in lifecycle events when present.
- A recovery or diagnostic pane without task id / dispatch context may only
  report diagnostics, blockers, or human escalation evidence. It must not emit
  dev/review/test/judge completion events.
- If board state and runtime evidence disagree, report the mismatch with
  task id, observed state, expected state, and evidence path or command.

## Required Output Fields

When reporting a state-relevant result, include:

- `task_id` or `feature_id`
- `role` and `instance_id`
- current phase
- dispatch id when present in the briefing
- intended next event
- evidence references
- known blockers or risks

## Stop Conditions

Stop and report instead of guessing when:

- no task id is available
- no dispatch id is available in a strict briefing
- the requested transition is not represented by current runtime state
- an update would require direct truth-file edits
- another role owns the next deterministic action
