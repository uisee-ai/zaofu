---
name: zf-prd-plan-synth
description: "Use for ZaoFu PRD workflows. Defines the standard scan -> Planner -> plan-critic flow, optional PRD authoring handoffs, and the dispatchable plan contract for product delivery."
---

# ZaoFu PRD Plan Synthesis

## Stage Contract

For the standard `PrdFlow` compiled by `workflow_profiles.py`, follow the
actual dispatch briefing and configured role names:

1. `prd-scan`: `product-scan` and `tech-scan` read in parallel; the Kernel
   waits for both and emits `prd.scan.completed`.
2. `prd-plan`: `planner` produces the complete Plan, task map and matrices.
3. After mechanical pre-submit admission, `plan-critic` runs as the stage's
   independent synth role. A correctable rejection returns to Planner;
   acceptance lets the Kernel publish `task_map.ready` and the bound package.
4. When enabled, human Plan approval precedes product writer dispatch.

There are two configured Agent roles in the strict Plan stage, or four
including its two scan roles. The Kernel is deterministic orchestration,
not an additional Agent. Retries and provider-native helpers are not new
configured workflow roles. Do not run Planner and Critic as parallel peers:
Critic needs the exact admitted Planner artifacts.

The author/PRD-review/task-map handoffs below also apply when an explicitly
configured workflow separates PRD authoring. They do not add `prd-author`,
`prd-critic`, or `task-map-synth` to the standard PrdFlow.

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
task needs singular `source_key`, `source_ref`, and `source_excerpt` anchors.
Optional `source_keys` / `source_refs` add evidence but do not replace those
primary fields. If provenance lives in `source_index.json`, every `task_id`
under `tasks[]` or `task_sources[]` must carry the same singular anchors.

Read the exact confirmed requirement/PRD authority artifact before slicing.
When it explicitly names task boundaries, task ids, dependencies, AC ownership,
or a confirmed/recommended workflow, preserve that decision map exactly unless
the current owner request explicitly asks for a redesign. A smaller task count
or a cleaner-looking merge is not permission to collapse those boundaries.
Derived matrices and scan summaries do not override the authority artifact.

For greenfield PRD builds, include top-level `schema_version: "task-map.v1"`,
`target_root`, and `shared_conventions` before emitting success. At minimum,
fix `test_path_prefix`, package/workdir root, package import name, and any
packaging/scaffold file such as `app/pyproject.toml`. These values must be
repo-relative even when verification commands run through `cd <target_root>`.

Verification execution-context rule (ZF-E2E-RACING-P2, 2026-07-11): each
task's structured `verification` command is machine-executed from the
repository root — it must run as-is from there. If it depends on
`target_root` or another subdirectory, embed the directory in the command
itself (`cd <subdir> && …` or an equivalent flag); a bare `npm test` with
`package.json` under `app/` fails from the root and burns rework rounds.
Keep the structured command identical to the one stated in the acceptance
text.

## Task Design

Split by end-to-end user journey, not by component-only layers. Each normal
slice should own the smallest runnable behavior across the API/runtime/web
layers it needs, plus its focused tests and evidence. A good task has:

- owner role such as `dev-api`, `dev-runtime`, or `dev-web`;
- `allowed_paths` and `exclusive_files`;
- dependencies and wave;
- acceptance criteria tied to the PRD;
- verification commands for product, API, and web verify agents.

Every mandatory user journey must map to one or more bounded tasks and exact
acceptance ids. Do not create independent "backend", "frontend", and "tests"
tasks that cannot demonstrate a user-visible outcome on their own. When more
than one slice feeds the same product, preserve one final assembly owner that
owns shared wiring and the real product entrypoint.

Each `exclusive_files` path has one owner across the full map. A later wave or
dependency does not permit duplicate ownership; assign shared entrypoint and
scaffold edits to one assembly/owner task.

For small products, a serial task map is often better than parallel horizontal
tasks:

- Wave 1: one scaffold task owns package metadata, directory layout, and
  conventions documentation.
- Later waves: feature tasks own their production files and matching tests.
- Final wave: one wiring/assembly task owns the real entrypoint and any package
  scaffold it changes, sets `root_owner_class: "assembly"` when more than one
  bundle feeds it, depends on prior feature tasks, and verifies the assembled
  product through the real entrypoint plus the full suite.

Do not emit a task map that places implementation in one task and its tests or
entrypoint smoke in another. Do not use `tests/...` paths when the declared
test prefix is `app/tests/...`; all owned paths stay repo-relative.

## Acceptance Evidence Satisfiability

Before emitting `task_map.json`, build an AC-to-evidence ownership matrix for
every mandatory acceptance criterion. Each row must identify:

- acceptance id and verification tier;
- the task that can produce the behavior;
- the task or Verify role that produces each required artifact/receipt;
- command ids and the real runner/method they invoke;
- configuration, specification, fixture, and output paths that must be
  writable;
- proof that those paths are covered by the producer task's `allowed_paths`,
  or by an explicit upstream dependency that already materializes the
  artifact.

Reject or revise the task map before handoff when a task is responsible for
evidence that it cannot legally produce. In particular, a final assembly task
may aggregate admitted evidence refs from prior slices, but it must not own
creation of browser traces, videos, screenshots, fixtures, or runner
configuration when those files belong to another task. Move evidence
production to the owning slice or expand/split ownership explicitly.

Declare `required_plan_ports` from the PRD's actual dependencies, normally
`requirement_spec`, `goal_claim_set`, `task_map`, `planning_result`, and the
matrices consumed by implementation or Verify. Emit their source refs; never
construct or select the current Plan Artifact Package yourself.

When the briefing declares `product_acceptance_spec` as a required plan port,
return one top-level `plan_ports[]` descriptor with logical name
`product_acceptance_spec`, schema `product_acceptance_spec.v1`, and an inline
body containing:

- one stable `assembly_owner` that matches the task-map assembly owner;
- runnable `entrypoints[]`, each with `entrypoint_id`, exact `start_command`,
  observable `health_check`, and owner;
- `user_journeys[]` with stable ids, mandatory flags, verification tiers, and
  observable assertions (`api|cli|dom|interaction|state|visual`);
- a `provider_qualification` policy that explicitly says whether real Provider
  qualification is required, which providers are in scope, and its TTL.

Do not invent run, plan-revision, or task-map-generation values in that body.
The Kernel binds those identities while constructing the current Plan Artifact
Package. Product semantics, journeys, commands, and assembly ownership remain
Planner-owned.

For `e2e` / `real_e2e` criteria, bind the command to the intended executable
path and method. A deterministic analytical estimate, fixture replay, or
mock-only receipt cannot satisfy a contract that requires the real application,
browser, provider, or simulation unless the acceptance criterion explicitly
permits that substitute.

For Docker Playwright rows, preserve the complete runner boundary in the
canonical command: use `--entrypoint bash -lc` (or another explicitly pinned
entrypoint) and include `acceptance_ids` on every real-E2E row. Do not emit
`mcp/playwright:latest node --test ...`; the image's default MCP entrypoint
will parse `node` as an option and the workflow will fail before the browser
starts. Keep static/runtime commands in the normal Test Matrix and do not label
them as real-E2E evidence.

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

When PRD delivery runs as a long-horizon goal loop, the runtime (not the PRD
roles) emits the goal lifecycle: `run.goal.started`, `run.goal.updated`,
`run.goal.completed`, and `run.goal.blocked`, plus
`run.goal.quiescent.entered` / `run.goal.quiescent.exited` when the loop idles.
Goal closure is the `run.goal.completed` signal, so a replan is truly done only
when a clean rescan lets the loop reach it instead of `run.goal.blocked`.
