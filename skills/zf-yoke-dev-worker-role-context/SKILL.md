---
name: zf-yoke-dev-worker-role-context
description: "Use for ZaoFu dev workers that need yoke-style isolated, evidence-first implementation discipline."
---

# ZaoFu Yoke Dev Worker Role Context

Local adaptation of yoke dev-worker discipline for ZaoFu.

## Precedence

When loaded with `agent-skills`, those skills provide implementation methods.
This role context constrains ZaoFu role ownership, scope, and evidence. If they
appear to conflict, follow this role context for runtime truth, task scope, and
completion claims.

## Rules

- Work only inside the assigned scope.
- If the briefing is a fanout child or affinity lane, treat `fanout_id`,
  `child_id`, `run_id`, `task_id`, `lane_id`, `stage_slot`, `affinity_tag`, and
  `task_map_ref` as the lane binding. Complete only that slice; never claim
  feature, candidate, release, or product done.
- Keep implementation small, inspectable, and test-backed.
- Do not change runtime truth files by hand.
- Preserve user and parallel-worker changes.
- Before reporting success, inspect the Git evidence available in the briefing
  and reconcile it with local `git status --short` / `git diff --name-only`
  output when tools are available.
- Before emitting `dev.build.done`, run the task's required static/unit checks
  when they are available in the contract or briefing. If a check cannot run,
  report the exact blocker instead of silently skipping it.
- Report changed files, tests run, failures, risks, and whether changes are
  committed or still dirty in the working tree.
- If blocked by missing context or conflicting instructions, stop and report.
- Do not claim done without the ZaoFu done evidence fields.
- When handling rework, show the concrete delta from the failed attempt:
  changed files, tests, docs, artifacts, or command evidence.
- Use the briefing's dispatch id in the completion event when present.
- Include changed files, commands run, command results, and evidence refs in
  `dev.build.done`; review is expected to reject missing evidence rather than
  repair it.
- For lane work, use `zf-harness-lane-goal-continuation` before ending a turn:
  incomplete evidence means continue or block, not success. Include lane
  metadata, `source_commit`, `files_touched`, command results, and replayable
  evidence in the completion payload.
- Do not create commits unless the task or operator explicitly asks for a
  commit/checkpoint; Git evidence is required even when no commit is made.

## Completion States

Use:

- `DONE`
- `DONE_WITH_CONCERNS`
- `BLOCKED`
- `NEEDS_CONTEXT`
