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

The Markdown plan is the human review document, not a one-paragraph status
summary. It must be long enough to let an owner review the confirmed scope
without opening the provider transcript. Include these sections whenever the
workflow has a source PRD or authority bundle:

- a human approval entry in this order: **PRD** (user outcome, scope, and
  non-goals), **SDD** (system boundaries, entrypoints, data/API contracts,
  ownership, and dependencies), **TDD** (acceptance-to-command mapping,
  evidence paths, and current PASS/BLOCKED/NOT_RUN state), risks/open
  decisions, and an approval checklist;
- authority and source index, with every source ref and digest-bound anchor;
- objective, users, scope, non-goals, assumptions, risks, and unresolved owner decisions;
- one row for every formal task with its group, wave, dependencies, owned paths,
  acceptance criteria, verification owner, and evidence contract;
- the dependency graph and parallel execution waves;
- requirement, acceptance, test, and real-E2E traceability summaries with their
  exact matrix artifact refs;
- approval gates and the explicit rule that impl/verify remain blocked before
  owner approval.

The approval entry is a contract, not a formatting preference. For
`derived_plan_full`, the Planner/Critic handoff must bind these semantic
fields through existing plan ports or explicit evidence refs:

- **PRD**: users, problem/outcome, scope, non-goals, success measures, and
  authority anchors;
- **SDD**: system slices, entrypoints, input/output or data/event contracts,
  ownership, dependencies, and failure semantics;
- **TDD**: acceptance ids, expected result, command id, verification owner and
  tier, evidence path, pass/fail rule, and current evidence state;
- **Decision Register**: stable decision id, question, options or recommendation,
  affected tasks, blocker impact, and current disposition.

Do not claim `derived_plan_full` when a section is represented only by a task
title, a source excerpt, or a command name. Preserve the missing contract as a
Critic rework finding. The operator may render a read-only dossier from these
bound artifacts, but Runtime must not invent project semantics or silently
select an owner decision.

## Semantic Coverage Contract

`derived_plan_full` is a semantic coverage claim, not a heading or row-count
claim. Before emitting it, build a coverage ledger in the existing requirement,
acceptance, or review artifacts; do not create a second task schema. Classify
the confirmed authority into the applicable source units:

- product outcome, scope, and non-goals;
- domain, module, capability, and user journey;
- behavior rule, state transition, input/output or data/event contract;
- integration, permission, failure, recovery, concurrency, performance, and
  other non-functional constraints;
- acceptance, test, real-E2E, and provider-qualification obligations.

Each source unit must have exactly one current disposition:
`covered`, `explicit_non_goal`, `pending_decision`, or `out_of_scope`. A
`covered` row keeps a stable source section/heading anchor, a meaningful
statement, the Plan section, requirement/acceptance ids, owning task(s), test
or command ids, evidence refs, and the owner or next stage. A non-goal,
pending decision, or out-of-scope row keeps its reason and impact; it cannot be
silently omitted. If the authority does not define domains or modules, record
that fact and the chosen grouping rationale.

Chapter-level refs may index an authority bundle, but they are not sufficient
as the sole proof for every behavior row. Prefer stable section/heading anchors
with a short excerpt or digest. Do not infer module coverage from task names,
file counts, or a shared statement such as “待执行/待审批追踪项”. Any
mandatory source unit without a disposition, semantic Plan body, producer, or
verification path is a Critic rework finding and blocks `derived_plan_full`.

Return `plan_content_profile: "derived_plan_full"` only when those sections and
the exact confirmed authority have been reviewed. Use `summary_only` when the
output is intentionally abbreviated; the operator UI must label that result as
a summary and keep it out of the full Plan approval path. Also return
`authority_refs` and `requirement_spec_ref` when the source authority is
available so the owner can open the original PRD beside the derived Plan.

For a source PRD that defines formal task or acceptance mappings, preserve the
mapping as data instead of paraphrasing it away. Keep the authority's exact
task boundaries, acceptance ownership, and source anchor that proves each
association. Do not round-robin acceptance ids, infer a new owner from task
order, or replace a requirement statement with a shared placeholder such as
“待执行/待审批追踪项”. Every trace row must retain its source id, a meaningful
requirement statement, owning task(s), and evidence status. If the source
mapping cannot be recovered, mark the Plan as needing rework and do not claim
`derived_plan_full`.

When the authority includes UI/UX acceptance, carry the named screens and
interaction boundaries into the relevant journey and integration task. Keep
product-specific screen names, page counts, interaction rules, task numbering,
and acceptance categories in the confirmed authority or project adapter; a
generic Plan skill must not invent them. “Page integration” without the named
boundaries supplied by the authority is an incomplete task slice.

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

## Human Revision Contract

The Preview is a review input and does not mutate the canonical Plan. A human
comment or change request must identify the current Plan revision and at least
one target source anchor, requirement/acceptance id, decision id, or Plan
section. The next replan records the requested change, old/new semantic diff,
affected decisions, tasks, dependencies, acceptance, and evidence, while
preserving unaffected completed tasks and their refs. Approval applies only to
the current revision; a change that affects scope, contract, task ownership,
dependencies, or acceptance invalidates the previous approval and requires a
new Critic verdict and owner approval. Do not silently edit a preview or reuse
an approval after its covered content changed.

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

## Human Review Before Approval

For the standard PRD Plan, apply `zf-plan-execution-readiness` and its
`references/human-review.md` before submission. Lead the accepted Plan with
user goals, scope/conditional scope, phase outcomes and owner decisions;
follow with module specifications and concrete acceptance scenarios. Keep
engineering repair history in appendices. When execution_spec is already
used, place shared reading material in `shared.human_review` and module
specifications/scenarios in each existing `tasks[].body.human_review`.
Preserve canonical task/AC/command IDs and bind these artifacts in the same
Plan Package. Critic checks semantic consistency; UI only renders the bound
content. Neither a page count nor repeated RED/GREEN prose proves clause
coverage. Never rewrite admitted artifacts to improve presentation.

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

An explicitly declared assembly task needs an `owner_role` distinct from its
dependency feature owners, even when waves are serial. Keep its task id as the
`product_acceptance_spec.assembly_owner`; do not confuse task ids with role labels.

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
- `user_journeys[]`, each with `journey_id`, `title`, `mandatory`,
  `verification_tier` (`runtime|e2e|manual_evidence`) and nonempty
  `assertions[]`;
- each journey's assertions with `assertion_id`, `statement`,
  `observation_kind` (`api|cli|dom|interaction|state|visual`), and `mandatory`;
- a `provider_qualification` policy that explicitly says whether real Provider
  qualification is required, which providers are in scope, and its TTL.

Check these nested fields together before the first validation. A prose
journey or an assertion using only `id`/`kind` is not the machine contract.
Use the current briefing's shape example to learn the fields, then replace
its example meaning with the confirmed requirement; never treat the example
as product evidence or use it to remove mandatory acceptance.

Do not invent run, plan-revision, or task-map-generation values in that body.
The Kernel binds those identities while constructing the current Plan Artifact
Package. Product semantics, journeys, commands, and assembly ownership remain
Planner-owned.

Use the complete descriptor example in `zf-plan-task-map-contract`'s
`references/product-acceptance.json` before writing this port. Populate every
mandatory journey from the PRD; the one-journey example is not a coverage limit.

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
