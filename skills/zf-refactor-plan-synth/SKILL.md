---
name: zf-refactor-plan-synth
description: "Use for ZaoFu RefactorFlow plan synthesis. Requires a lane-pipeline-compatible task_map with assembly/root ownership so implementation, review, verify, and judge stages can dispatch deterministically."
---

# ZaoFu Refactor Plan Synthesis

## Goal

Convert scan/review artifacts into a refactor plan that can pass deterministic
lane-pipeline admission. The output is not only a markdown plan; it is the
source of truth for lane dispatch.

## Required Inputs

- `review_artifact_ref` from the scan/review fanout.
- Coverage matrix, findings, uncovered areas, and scan evidence.
- `plan_intent` if provided by the triggering event.
- Lane count and assembly task declared by the workflow.

## Required Outputs

Write and emit:

- `refactor-plan.md` via `plan_artifact_ref`.
- `task_map.json` via `task_map_ref`.
- `source_index.json` or scan evidence refs via `source_index_ref`.
- `scan_quality_audit_ref` when scan quality was checked.
- `risk-register.json` and backlog candidates when useful.

All refs needed by the next stage must be top-level payload fields and included
in `artifact_refs` or `evidence_refs`.

## Source Index Rules

The implementation fanout consumes per-task provenance before dispatch. For
every task in `task_map.json`, include either:

- direct task anchors: `source_key`, `source_keys`, `source_ref`,
  `source_refs`, or `source_excerpt`; or
- a `source_index.json` entry under `tasks[]` or `task_sources[]` with the same
  `task_id` and non-empty source anchors.

Good refactor anchors point to scan findings, audit findings, PRD sections, or
plan sections, for example `scan/findings.json#F-023` or
`docs/plans/refactor-plan.md#lane-runtime`. A global `sources[]` list is not
enough for multi-task refactors.

## Lane Task Map Rules

Every task must be dispatchable:

- `task_id` is stable and unique.
- `affinity_tag` maps to a lane or ownership class.
- `wave` and `dependencies` define order.
- `allowed_paths` lists every path the worker may touch.
- `exclusive_files` lists non-shared files, or the task explains why it must be
  serialized.
- `verification` names concrete commands or evidence checks.

If the workflow declares an assembly task, the task map must either include
that exact task id or include a task with `root_owner_class: "assembly"`.

Any task that owns scaffolding such as `package.json`, `pnpm-lock.yaml`,
`tsconfig.json`, `vitest.config.ts`, or root build config must include those
paths in `allowed_paths`. This prevents root workspace changes from being
unowned during lane execution.

## Completion Check

Do not emit plan success until:

1. `task_map.json` contains the assembly/root owner requirement.
2. Each lane has complete allowed paths and verification.
3. `scan_quality_audit_ref` is present or a clear failure reason is emitted.

## Goal Closure Loop

Refactors must close parity, not merely finish the first task map. After verify,
use:

- `zf-verify-rescan-replan` to rescan the produced code against the original
  system and the scan matrix.
- `zf-goal-closure-replan-contract` with `goal_kind: "refactor"` and
  `gap_category: "parity_gap"` to produce `goal-gap-plan.v1`.
- `zf-gap-task-synth` to append precise missing parity tasks through the normal
  `task_map.amended` / `task_map.ready` bridge.

Gap tasks should reuse the original module/lane affinity when possible and
should preserve source refs to both original implementation paths and produced
target paths. Do not pass judge while any P0/P1 parity gap remains open.
