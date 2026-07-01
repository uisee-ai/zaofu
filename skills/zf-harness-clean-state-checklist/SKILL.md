---
name: zf-harness-clean-state-checklist
description: "Use before requesting ZaoFu runtime cleanup; requires dry-run, archive guard, and project.state_dir awareness."
---

# ZaoFu Harness Clean-State Checklist

Clean-state operations are destructive and must be deterministic runtime
actions. This skill is only a checklist.

## Before Cleanup

Check:

- resolved `project.state_dir`
- active or running workers
- unarchived run evidence
- lockfiles and skill provenance status
- whether archive is required first
- whether cleanup is dry-run or confirmed

## Rules

- Prefer dry-run first.
- Never hard-code `.zf` when project context can resolve the state dir.
- Do not delete runtime directories by hand.
- Do not clean while workers are running unless an explicit operator action
  allows it.
- After cleanup, validate cold-start readiness before treating the workspace as
  recovered.
