---
name: zf-plan-task-map-contract
description: "Use in ZaoFu plan/task-map synthesis roles. Defines the fixed machine contract that downstream writer fanout, Kanban, verify, and judge stages consume. Customer/domain skills may be added, but they do not replace this contract."
stages: [plan, replan, scan, triage]
tags: [contract, task-map, artifact]
auto_inject: false
load_on_demand: true
---

# ZaoFu Plan Task-Map Contract

Absorbs `zf-decision-map-replan`.

This is an on-demand machine-contract reference for
`zf-yoke-planner-role-context`, not a second always-active planning method.
Read it immediately before writing a task map. The current briefing's output
path, compiled schema education, and admission diagnostics are authoritative;
do not copy an older example when they differ. Runtime owns schema/admission,
while the planner owns task meaning, slicing, acceptance, dependencies, and
source traceability.

When `product_acceptance_spec` is required, read
[the complete descriptor example](references/product-acceptance.json) before
authoring the result. Adapt its commands, owners, journeys and assertions to
the actual requirements; it demonstrates field names, not product scope or a
default provider waiver. Keep `journey_id` and assertion-local `assertion_id`
distinct; `id` and `kind` are not substitutes for those schema keys.

## Mechanical Closure Before Output

Before emitting a plan result, reconcile the final serialized artifacts as
sets. Do this after all prose edits and before the success event:

1. `task_map.tasks[].task_id` must equal the task ids covered by
   `source_index.tasks[]` / `task_sources[]`; capability and dependency
   `task_ids` must all exist in that same Task set.
2. Every mandatory acceptance criterion must have one stable id and its
   capability/requirement mapping must resolve to the owning Task.
3. For each acceptance id, `verification_command_ids` must exactly equal the
   global canonical `validation.commands[].id` values whose `acceptance_ids`
   include that id. At least one linked command `owner` must equal the
   criterion's `verification_owner`; additional linked commands may belong to
   a later layer such as `candidate_verify`.
4. Acceptance matrix, test matrix, real-E2E matrix, and Task-local verification
   lists must reference the same canonical command registry. Their command ids,
   acceptance-id sets, owners, tiers, and literal command bodies may not drift.
5. Every Task must map to its declared capabilities and every capability
   `task_id` must map back to an existing Task; compare both directions.
6. On replan, preserve every prior mandatory `goal_claims[]` contract unless
   the controlled inputs include an active claim waiver. Copy the canonical
   `id`, statement, `mandatory`, `acceptance_ids`,
   `verification_command_ids`, `verification_owner`, and
   `verification_tier` values from the prior Task Map exactly in meaning; do
   not keep only the id/text while weakening its closure mapping. Completed
   implementation may move to an immutable baseline, but that does not
   authorize deleting, downgrading, renaming, or rewriting its Goal claim.
   Read the prior Task Map and pinned Goal Claim Set supplied by the briefing,
   preserve old mandatory claim records, then add only the new gap claims.
7. If `plan-rework-context` contains `human_resolution`, treat its exact
   `response`, action, source event, and contract evidence refs as current
   owner authority. Bind `events.jsonl#<source_event_id>` in the revised plan
   sources and satisfy the response literally; an older owner confirmation or
   prior approval does not override this causal resolution.
8. If the briefing provides a local validator or admission command, run it on
   the final files and repair every finding before submission. Do not invent a
   validator command or substitute an equivalent command for the one supplied.

Do not submit a knowingly inconsistent first draft and rely on a rework turn
to discover these set mismatches. This preflight is mechanical closure; it does
not replace critic judgment or product acceptance.

Keep `task_map.json`, `source_index.json`, and each `plan_ports` JSON body
pretty-printed and structurally formatted while authoring them. Never serialize
a mutable candidate as one compact line. Parse the complete result once before
invoking the supplied validator. When validation returns multiple findings,
repair the full diagnostic set in one edit and parse once before the next
validation. Repeated comma, quoting, path, or one-finding-at-a-time validator
turns consume the workflow operation budget without adding planning evidence.

## Product Boundary

为多模块需求编写 Task Map 时，遵循
[通用模块与任务关联](references/module-graph.md)，输出显式模块、需求和任务关系。
模块语义由 Planner 决定；图布局与运行状态由代码从已接纳工件和事件派生。

ZaoFu plan stages have two output layers:

- Human layer: durable markdown for review and discussion.
- Machine layer: `task_map.json` referenced by `task_map_ref`.

Customer or project skills may influence domain judgment, but the machine
contract is fixed. Do not rely on markdown-only task lists for dispatch.

## Required Artifacts

Write durable artifacts before emitting the stage success event:

- `plan_artifact_ref`: markdown plan, normally under `docs/plans/` or the
  fanout artifact directory inside the assigned workdir.
- `task_map_ref`: JSON task map consumed by writer fanout.
- `source_index_ref`: JSON or markdown evidence/source index when source facts
  were inspected.

Include these paths in `artifact_refs` / `evidence_refs` as appropriate.
All output refs are workdir-relative. Do not write `.zf/` or another absolute
state path directly; the Kernel relocates admitted refs and computes canonical
digests.

When a result returns more than one Task Map representation, the inline
`task_map`, file resolved by `task_map_ref`, and `task_map` body/ref in
`plan_ports` must be the same JSON document. Runtime compares canonical
digests across every producer and returns `task_map_producer_mismatch` on any
stale copy. Correct every representation before pre-submit validation; never
rely on producer precedence.

Default to exactly one Task Map producer: the complete JSON file referenced by
top-level `task_map_ref`. That ref satisfies the logical `task_map` Plan port
when the active result profile supports derivation. Do not add a descriptor,
summary, `ref`, or `producer_task_ids` placeholder as `plan_ports.task_map`;
it is a second, different Task Map producer. Emit a `task_map` Plan-port body
only when the output contract explicitly requires another complete
representation, and then copy the exact canonical JSON document byte-for-byte
in meaning before validation.

## Plan Artifact Ports

Emit every plan-level input as an explicit top-level ref. Use canonical logical
names when the result profile supports descriptors: `requirement_spec`,
`task_map`, `planning_result`, `source_index`, and applicable matrices. Legacy
`prd_ref`, `product_spec`, and `issue_ref` remain valid producer fields during
migration, but do not invent a ref or scan a directory to fill a missing port.

`required_plan_ports` in `task_map.json` names logical inputs only. Do not
write a Plan Artifact Package id/ref, choose which Package is current, or emit
Package lifecycle events; the Kernel derives the Goal Claim Set and admits the
immutable Package after validating these explicit refs.

The declaration is an executable downstream input contract:

- use canonical logical names and keep the list non-empty when it is present;
- declare only ports actually needed by Impl/Verify, including applicable
  matrices;
- emit a real top-level ref for every declared port in the Plan result;
- do not declare a port whose project-specific draft is still unadapted or
  whose ref cannot be hydrated.

After Package admission, the Kernel binds these ports to the current
generation and exposes them as required controlled reads. Planner owns the
selection and semantic readiness; it does not self-select a Package.

### Large supplemental specifications

For large supplemental behavior/design inputs, an explicit additional port such
as `execution_spec` may use `task-scoped-plan-input.v1`:

```json
{
  "schema_version": "task-scoped-plan-input.v1",
  "shared": {"protocol": "versioned shared rules and source references"},
  "tasks": [
    {"task_id": "T-1", "body": {"behavior": "complete owned assertions and dependencies"}}
  ]
}
```

Use exactly these top-level keys and row keys. Declare every consumer Task's
exact id once, with an object body; missing/duplicate partitions fail closed.
Put cross-task rules and required interfaces in `shared` or the consuming Task's
body with same-version source refs. Impl/Task Verify must read both shared and
owned partitions; Candidate Verify reads the entire port. The complete original
artifact and digest remain authoritative. This is an input artifact, not another
task contract. Existing ports retain full reads; do not wrap fixed-schema
acceptance/test matrices or silently discard requirements to reduce context.

## Source Provenance Contract

Every dispatchable task must be traceable back to the plan, scan, PRD, issue,
or research evidence that caused it. Strict/release workflows fail closed when
per-task provenance is missing.

Every task must include these singular task-level anchors:

- `source_key`: one stable anchor string.
- `source_ref`: one file/section reference.
- `source_excerpt`: a short evidence excerpt tied to the task.

`source_keys` and `source_refs` may add supporting evidence, but they do not
replace the singular primary anchor. Keep `source_key` and `source_ref`
semantically paired: both must identify the same primary fact or plan section.

If the task itself is intentionally compact, `source_index.json` must map every
`task_id` through `tasks[]` or `task_sources[]`:

```json
{
  "schema_version": "source-index.v1",
  "tasks": [
    {
      "task_id": "PDD-CORE-001",
      "source_key": "prd.md#acceptance-realtime-sync",
      "source_ref": "docs/plans/example-prd.md#acceptance",
      "source_excerpt": "Realtime sync is a mandatory owner acceptance.",
      "source_keys": ["prd.md#acceptance-realtime-sync"],
      "source_refs": ["docs/plans/example-prd.md#acceptance"]
    }
  ]
}
```

`source_facts[].task_ids[]` is accepted as compatibility input, but prefer the
task-level `tasks[]` / `task_sources[]` mapping above. Do not rely on a global
`sources[]` list except for legacy one-task issue plans.

## Success Payload

Emit the required refs at the top level of the event payload. It is acceptable
to duplicate them inside `report`, but top-level fields are the dispatch
contract.

```json
{
  "fanout_id": "<fanout id>",
  "stage_id": "<stage id>",
  "child_id": "<role or synth>",
  "status": "completed",
  "summary": "<one-line plan result>",
  "pdd_id": "<stable product/work id>",
  "feature_id": "<stable feature id>",
  "plan_artifact_ref": "docs/plans/example-plan.md",
  "task_map_ref": "artifacts/plan/task_map.json",
  "source_index_ref": "artifacts/plan/source_index.json",
  "artifact_refs": [
    "docs/plans/example-plan.md",
    "artifacts/plan/task_map.json"
  ],
  "evidence_refs": [
    "artifacts/plan/source_index.json"
  ],
  "report": {
    "child_id": "synth",
    "status": "passed",
    "summary": "<same outcome>",
    "findings": [],
    "recommendation": "approve"
  }
}
```

## Task Map Shape

`task_map.json` must contain dispatchable tasks:

```json
{
  "schema_version": "task-map.v1",
  "pdd_id": "<stable product/work id>",
  "feature_id": "<stable feature id>",
  "target_root": "<repo-relative product root, when the workflow has one>",
  "shared_conventions": {
    "test_path_prefix": "<repo-relative test prefix, e.g. app/tests/>",
    "package_root": "<repo-relative package/workdir root, e.g. app>",
    "packaging_file": "<repo-relative scaffold file, e.g. app/pyproject.toml>"
  },
  "source_refs": {
    "plan_artifact_ref": "docs/plans/example-plan.md",
    "source_index_ref": "artifacts/plan/source_index.json"
  },
  "tasks": [
    {
      "task_id": "PDD-CORE-001",
      "title": "Implement one vertical slice",
      "summary": "Concrete behavior and scope.",
      "owner_role": "dev-core",
      "skills_required": ["project-domain-skill"],
      "affinity_tag": "core",
      "wave": 1,
      "dependencies": [],
      "allowed_paths": ["src/example/**", "tests/example/**"],
      "exclusive_files": ["src/example/service.py"],
      "acceptance": ["PDD-CORE-001-AC1: service returns the documented result"],
      "verification": ["uv run pytest tests/example/test_service.py"],
      "source_keys": ["docs/plans/example-plan.md#slice-core"],
      "evidence_contract": {
        "static": "changed file summary",
        "runtime": "pytest output for tests/example/test_service.py",
        "expected_red": false
      }
    }
  ]
}
```

When the workflow changes an existing non-default target, resolve that ref and
write its immutable commit at top-level `target_commit`. This is the writer
checkout baseline. Do not leave the only baseline binding under `metadata`,
inside a source ref, or in markdown prose. Omit `target_commit` only when the
configured default candidate base is intentionally the implementation base.

Every task must be small enough for one focused worker, have explicit ownership,
list dispatch-safe paths, and include verification evidence. If a task touches
workspace scaffolding such as `package.json`, `pyproject.toml`, `setup.py`,
`setup.cfg`, `tsconfig.json`, lockfiles, or root build config, it must own
those paths explicitly. For greenfield products under a subdirectory, treat that
subdirectory's package scaffold (for example `app/pyproject.toml`) as the
project scaffold owner and list it in both `allowed_paths` and
`shared_conventions.packaging_file`.

`skills_required` is the Planner-owned, machine-readable method contract for a
Task. When the effective role/skill inventory names a project or domain skill
that the Task must use, copy that exact registered skill name into this list.
Use a JSON list of unique, non-empty strings. Do not encode skills only in
`owner_role`, prose, markdown, or `affinity_tag`, and do not invent an
unregistered skill from the Task title. Omit the field when no task-specific
skill is required. The Kernel validates and preserves this declaration through
canonical Task materialization and replan; it does not infer semantic skills.

Every path in `exclusive_files` has exactly one owner across the entire Task
Map. A dependency edge, later wave, or serialized execution does not permit a
second exclusive owner. If two planned changes need the same file, combine
them under one owner task (often assembly) or keep a truly read-only/shared
path out of `exclusive_files` and describe that boundary explicitly.

Writer `allowed_paths` are exclusive writable capabilities, not a read
manifest, and must not overlap across tasks, including tasks separated by
dependency edges or waves. When two proposed tasks both need to change one
integration file, combine them under one task or assign that file to one
integration owner and keep the other task's work independent. Do not add a
sibling-owned file to another task's `allowed_paths` merely because its tests
read that file; plan-wide verification may read sibling-owned paths without
copying them into the current task. Pre-submit validation applies the same path
ownership evaluator as writer fanout admission and reports
`writer_fanout_task_map_invalid` before submission.

Do not make a scaffold task own or verify a placeholder file (`.gitkeep`,
`.keep`, `.placeholder`) below a subtree owned by another task such as
`app/static/**`. The admission layer delegates that placeholder to the subtree
owner to avoid overlapping writers. Therefore the scaffold task must not cite
the delegated file in its acceptance criteria or verification commands. Usually
the correct plan is to omit the placeholder entirely because the subtree owner
will create real files; if the placeholder is itself a required deliverable,
assign it and its acceptance check to that same subtree owner.

`evidence_contract` must be a JSON object (dict), never a list. The kernel
contract layer only accepts dict values — `contract.update` ignores a non-dict
`evidence_contract`, the minting path replaces a non-dict with `{}`, and the
discriminator consumes dict keys such as `expected_red` — so a list here is
silently dropped.

When using structured `validation.commands[]`, every command needs a stable
`id`, executable `command`, linked `acceptance_ids`, `owner`, `tier`,
`deterministic`, `reusable`, and `timeout_seconds`. Use only the canonical
task-map tier vocabulary:

- `static` for setup, dependency install, lint, typecheck, and build checks;
- `runtime` for unit, contract, integration, and non-browser behavior checks;
- `e2e` for browser/application end-to-end checks;
- `manual_evidence` only when an acceptance clause genuinely requires a human
  evidence verdict.

Do not invent a `setup`, `browser`, or `real-e2e` tier. Runtime normalizes a
small compatibility alias set, but unknown tiers fail closed before writer
dispatch.

Each `real_e2e_matrix.rows[].command_id` must resolve to a canonical command
whose tier is `e2e` or `manual_evidence`. Keep `static`/`runtime` commands in
the Test Matrix. If a command actually exercises the built application end to
end, classify it consistently as `e2e` in the registry and all references;
do not merely relabel a unit suite to satisfy this gate.

When the active v4 Task Pipeline profile requires rolling smoke, mark at least
one cheap command per task with JSON boolean `rolling_smoke: true`. The marked
command must be deterministic, reusable, and use the `static` or `runtime`
tier. `rolling_smoke` selects a command from the canonical registry; it is not
a fifth verification tier.

A `reference_only` row, `command_refs`, or `rolling_smoke_command_ids` entry
without an executable `command` in that task does not satisfy this gate. When
a task does not own a suitable acceptance command, define one unique
task-local smoke id (for example `TS-<task>-smoke`) with an executable command,
`acceptance_ids: []`, one producer task, and `rolling_smoke: true`. Do not copy
another task's canonical VC definition merely to satisfy rolling smoke.

**Canonical command identity rule (Refactor R2, 2026-07-29):** downstream
Impl, Candidate, and Verify execute the stored `command` as-is. A Planner must
not prove a rewritten, re-escaped, or merely "equivalent" command and then
attach that result to the canonical command id. Avoid escape-sensitive inline
programs such as nested regular expressions in `node -e` / `python -c`;
put the assertion in a repository test or script owned by the same vertical
slice, then declare the stable test/script command. Before emitting, inspect
the final serialized command string, not an unescaped source-language draft.
For a contract-bound passed Verify result, every mandatory acceptance command
must have an exact-target passing receipt. Preserve the registry's
`command_id`, literal `command`, and `command_digest`, bind it to the exact
`target_commit`, and report `status: passed` plus `exit_code: 0`. A generic
probe or output from a substituted command is supplemental evidence only and
does not satisfy canonical command coverage.

The corresponding Test Matrix has one and only one command registry:
`test_matrix.commands[]`. Repeat each Task Map command there with the same id,
exact command string, and `acceptance_ids`. Do not define a second command
under `test_matrix.tests[]`; other matrix rows reference command ids.

Each command id has exactly one producer Task in the immutable Task Map.
Acceptance criteria in any Task may reference that global id directly. Do not
copy the definition into consumer tasks: Product Delivery and Writer
materialize referenced definitions into derived Task Contracts without
changing the Task Map or Plan-port digest.

**Candidate composition monotonicity rule:** every task-contract command runs
again after all task refs are integrated. A focused task command must therefore
remain valid when later tasks add files or tests. Target the exact owned test
file or stable script; do not combine broad test auto-discovery with a name
filter whose behavior depends on which unrelated files happen to exist. Put
candidate-wide discovery commands under `candidate_verify` ownership. Before
emitting, check the command against the planned final file set, not only the
task's pre-integration worktree.

Structured `acceptance_criteria[]` must use one of the four canonical
`verification_owner` values consumed by Task Contract snapshots:

- `impl_self_check` for evidence produced by the implementation attempt;
- `task_verify` for independent task-level test, review, scope, and contract
  checks;
- `candidate_verify` for integrated candidate, assembly, or final judge checks;
- `human` only for an explicitly owner-held decision.

Do not use role labels such as `dev`, `verify`, `review`, `contract`, or
`judge` as `verification_owner`. Runtime retains bounded compatibility aliases
for older plans, but new task maps must emit the canonical values so plan
admission and durable operation preregistration consume the same contract.

For every mandatory acceptance criterion, at least one linked canonical
`validation.commands[]` entry must have an `owner` equal to that criterion's
`verification_owner`. Keep this owner binding identical in the task map,
acceptance matrix, test matrix, and real-E2E matrix. A command owned only by
`candidate_verify` cannot satisfy a criterion owned by `task_verify`, even when
the command text would exercise the same behavior.

Choose the Task Pipeline entry explicitly when a task has no implementation
delta. These are scheduler semantics, not permission to weaken evidence:

- A normal writer task omits `execution_mode` (or uses `standard`) and declares
  non-empty `allowed_paths`; it follows Impl then task Verify.
- A genuinely human-owned gate uses `execution_mode: "external_gate"`, exact
  `required_manual_evidence`, `allowed_paths: []`, and mandatory criteria owned
  by `human` at `manual_evidence` tier. Do not create an implementation task to
  run an interactive command or fabricate the receipt. Bind the task to an
  immutable `continuation_checkpoint` such as `git:<full commit>`.
- A write-free candidate audit uses `execution_mode: "runtime_only"` (the
  scheduler normalizes it to `verify_only`), `runtime_only: true`,
  `write_free: true`, `allowed_paths: []`, and exact `candidate_target` plus
  `immutable_baseline`. Its mandatory criteria use `candidate_verify`. A failed
  audit must not silently become an Impl rework of the same read-only task.

When a candidate audit depends on an external gate, put the gate task in
`blocked_by` and declare `required_external_digest_env` in the audit's
`evidence_contract`. The audit command may read that environment variable; the
Kernel binds its value from the admitted upstream receipt. Keep upstream
commands in the global registry with their original `producer_task_id`, but do
not rerun them from the audit task. The audit executes only commands produced
by itself and owned by its verification layer; retained upstream receipts are
read-only evidence.

An admitted, completed E2E run may be reused without redispatch only as an
immutable baseline. In that case the criterion must use
`evidence_mode: "immutable_baseline_only"`, tier `e2e`, an empty
`verification_command_ids` list, and evidence refs containing both the exact
`git:<target_commit>` and retained artifact paths. The matching real-E2E matrix
row must use `execution_mode: "immutable_baseline_only"`,
`command_required: false`, the same `target_commit`, the exact
`origin_command`, and the retained receipts. A runtime aggregation/audit may
consume those receipts downstream, but its command id and row must remain in
the runtime test registry; never place that runtime command in the real-E2E
matrix or use it as the same-tier verifier for the baseline criterion.

Give every acceptance entry a stable id by prefixing the string (e.g.
`PDD-CORE-001-AC1: ...`; the kernel accepts `acceptance_criteria` as an
equivalent field name). The verify stage's `requirement_coverage_matrix` rows
must cite a `requirement_id` taken from the task contract's acceptance
clauses — if the plan does not mint stable acceptance ids, the verify matrix
has nothing to reference.

**Verification timing rule (avbs-r4 lesson)**: a bundle task's `verification`
may only contain commands that can pass with THIS task's own outputs plus the
pre-existing scaffold — unit tests scoped to the task's test dirs plus the
build. Do NOT put end-to-end (Playwright/browser) execution into parallel
bundle tasks: app wiring belongs to the assembly task, so per-lane e2e is
structurally unpassable at impl time and burns rework rounds. E2e execution
belongs in the assembly task's verification (and the verify stage). Writing
the e2e SPEC files is still a bundle deliverable; executing them is not.
Also: `schema_version` must be exactly `task-map.v1`, and verification
commands must not reference paths outside the task's
`allowed_paths`/`exclusive_files` — with one allowance: paths owned by a
sibling task in the same plan (any sibling's `allowed_paths` /
`exclusive_files`) are accepted, because verification runs tests read-only
and cross-task verification is legal. Docker-style mounts like `$PWD:/work`
are rejected by admission. If a command changes directory, still keep
`allowed_paths`, `exclusive_files`, `shared_conventions.test_path_prefix`, and
`target_root` repo-relative; for example `cd app && python -m pytest tests -q`
is valid only when the task owns `app/tests/...` and the map declares
`target_root: "app"`.

A path is part of this check even when the Planner expects the command to read
it without changing it. Before emitting the task map, inventory every literal
repo-relative file or directory operand in every verification command. Bind
each operand through this task's `allowed_paths`, this task's
`exclusive_files`, or a sibling task's declared paths. For an existing
validation-only regression file, assign it once to the task that owns that
verification boundary, omit it from every other task's write scope, and state
in `explicit_non_goals` that it must not be modified. Never duplicate a
read-only operand across task scopes; sibling ownership satisfies the read
contract without creating overlapping writers.

When a verification command also names legitimate host, temporary, container,
or other read-only paths that no writer task owns, declare those exact operands
in `verification_read_paths` at the **task object level**, as a sibling of
`allowed_paths`, `exclusive_files`, and `validation`. Do not place
`verification_read_paths` inside `validation`; the Task Map schema ignores it
there. Do not use `*`/`**` as a blanket escape and do not rewrite an
owner-provided verification command into a wrapper merely to evade path
admission. Preserve the command, copy the validator-reported read operands into
the top-level list, then run the local pre-submit validator again.

**Verification execution-context rule (ZF-E2E-RACING-P2, 2026-07-11)**: the
structured `verification` command is machine-executed from the repository
root (ContractD). It must run as-is from there — if it depends on a
subdirectory, embed the directory in the command itself (`cd <subdir> && …`
or an equivalent flag such as `--prefix`). A bare `npm test` whose
package.json lives in `app/` fails from the root and burns rework rounds.
The structured command and the acceptance-criteria text must state the same
command — re-check them against each other before emitting.

## Decomposition Rule: Vertical Slices, Not Horizontal Layers

Decompose by **vertical slice** — a behavior together with the production code
AND its own tests in the SAME task — exactly like the `core` task above whose
`allowed_paths` covers both `src/example/**` and `tests/example/**`. Split by
independent behavior/surface (e.g. core API vs web view), never by file type
(code vs tests vs config).

Hard rules (a writer fanout enforces `allowed_paths` and rejects out-of-scope
commits — a bad split dead-ends at `integration.failed`):

- A task that implements a source module OWNS that module's unit tests. Never
  put the implementation in one task and its tests in a separate task or writer
  lane. The implementer commits code and its tests together, so an
  implementation task's `allowed_paths` MUST include both the source paths and
  the matching test paths (e.g. `["src/ledger.js", "test/**"]`).
- Do not create a code-only task plus a test-only task for the same feature.
  Two writers cannot share the same test files without colliding.

Splitting method beyond these hard rules (tracer bullet, prefactoring, shared
contract slices) lives in `yoke/vertical-slicing` — evolve the methodology
there; this section only pins the contract-level rules.

## Workflow Assembly Contract

When the briefing contains `refactor_contract`, obey it before applying the
parallel-bundle heuristic:

- `assembly_policy: "declared_task"` means the task map must include
  `assembly_task_id` exactly or include one task with
  `root_owner_class: "assembly"`.
- `assembly_policy: "none"` means the plan is a single serial bundle and MUST
  omit an assembly task. Do not create an `ASM-*` task or
  `root_owner_class: "assembly"` under this policy. If the plan needs two or
  more parallel bundles, first change the contract to
  `assembly_policy: "declared_task"` and reserve a distinct assembly owner,
  or coarsen the work into one serial bundle. Never emit a parallel map plus
  an assembly task while the contract remains `none`, because that creates an
  owner collision/self-lock.
- Root scaffold ownership is an explicit task-map fact. Set
  `refactor_contract.workspace_root_owner_required: true` (or a top-level
  `workspace_root_owner_required: true`) only when this delivery must change
  or validate a root-level scaffold/entrypoint, and assign that root path to a
  task. Omit it or set `false` for an imported-project/local patch. This does
  not relax task schema, path, evidence, or multi-bundle assembly validation.
- Do not emit success when a declared workflow assembly task is missing. Emit
  the configured failure event with the missing id and the proposed fix.

### Gate rejection feedback (mandatory)

If artifact/admission validation rejects a candidate plan, the next plan
attempt MUST read `artifact-gate-diagnostics.json` (or the equivalent
`diagnostics_ref`) and make a concrete delta addressing every reported
contract violation. An `assembly_policy=none` rejection must be repaired by
removing the assembly task and serializing the map, or by changing the
contract and assigning assembly to a distinct role. Re-emitting the same
invalid map, or triggering a fresh scan without carrying the diagnostics into
plan context, is not recovery and must not be reported as plan progress.

## Assembly Task: Required When the Plan Has >1 Parallel Bundle

Parallel writer bundles each build in isolation, so NOTHING owns the
cross-bundle wiring or the end-to-end behavior. That gap is invisible to each
bundle's own unit tests and only surfaces at candidate verification as
`integration.failed` / `test.failed` — e.g. the assembled CLI is non-functional
because no task owned the root entrypoint that wires the bundles together.

Whenever the plan has more than one parallel bundle, emit exactly one final
**assembly task**:

- Give it an `owner_role` distinct from the feature tasks it depends on
  (for example, `dev-assembly` versus `dev-feature`). A different task id or
  later wave does not remove this constraint, even in a serial map. These are
  logical ownership labels, not permission to create extra runtime roles.
- `root_owner_class: "assembly"`. The deterministic plan gate checks for this;
  its absence is flagged `缺 assembly 任务`. Root-level paths (package
  entrypoint, `__init__`, CLI `main` wiring, `pyproject.toml`
  `[project.scripts]`, top-level glue) may ONLY be owned by this task.
- `dependencies`: every bundle task id. `wave`: the last wave (it lands after
  all bundles complete). A bundle "completes" when its writer closes the slice
  with `dev.build.done` — or, in controller/profile workflows, the canonical
  `impl.child.completed` (`impl.child.failed` on failure), which the kernel
  treats as equivalent to legacy `dev.build.done`.
- `verification`: an END-TO-END smoke that invokes the assembled product
  through its real entrypoint AND runs the full test suite — not one bundle's
  unit tests. This is the task that catches cross-bundle integration defects
  before verify does.
- `acceptance`: "assembled product runs end to end; full suite green".

The assembly task counts toward the writer pool budget: you are still bounded
by `tasks <= writer pool size`, so reserve one slot for it (e.g. for a 3-writer
pool, produce at most 2 feature bundles + 1 assembly). Coarsen the feature
bundles before you drop the assembly task — the assembly task is not optional
when bundles run in parallel. A single-bundle / simple-serial plan is its own
owner and needs no separate assembly task.

For small greenfield PRD/issue products, prefer a serial scaffold → feature →
assembly/wiring wave plan over parallel horizontal slices. If you choose not to
emit a separate assembly task, the final wiring task must own the package
scaffold/entrypoint paths and explain why the plan is serial rather than
parallel.

## Fail Closed

Do not emit `task_map.ready` if `task_map_ref` is missing, unreadable, or points
to markdown. Emit the configured failure event with a concrete reason instead.

Do not emit `task_map.ready` in strict/release workflows when any task lacks a
task-level source anchor and no `source_index_ref` maps that task id to anchors.

Plan fingerprint dedupe: when plan approval is enabled, minting a plan whose
stage + pdd + task set matches a still-pending plan is suppressed — the kernel
emits `plan.minting.suppressed` (`reason: pending_plan_same_fingerprint`, with
`duplicate_of` naming the pending plan) and holds the new plan instead of
minting it. A replan must materially change the task set or wait for the
pending plan to be approved/rejected; re-emitting the same map is not a retry.

Rework quarantine: once candidate rework for a pdd has escalated
(`human.escalate` from `integration.failed` / `review.rejected` /
`test.failed` / `judge.failed`), a later `task_map.ready` for that `pdd_id`
does not auto-restart implementation unless its payload carries
`operator_authorized: true` or `rework_of`; without one of those it is
refused as `candidate.rework.quarantined`.

## Decision Map Sidecar (Optional)

Absorbed from `zf-decision-map-replan`. During plan and replan, optionally
capture fog-of-war in a decision map: why the plan is shaped the way it is,
what remains unknown, and which probes or replan triggers matter. It is
evidence/context for humans and the orchestrator, never the dispatch
contract — `task_map.json` (this skill) stays the only dispatch contract.

Hard rules:

- Do not create a second task schema.
- Do not set kanban status from a decision map; kanban transitions flow from
  events and kernel APIs only.
- Do not treat provider transcript as decision truth.
- Do not erase rejected options without source refs; repeated debate is a
  long-horizon cost.
- If a blocker changes task dependencies, route the change through the
  canonical replan channel below rather than encoding dependencies only in
  this artifact.

Write a markdown artifact for human review (it belongs to this skill's Human
layer output) and, when useful, a JSON sidecar. `zf.decision_map.v1` and
`decision_map_ref` are skill-owned conventions — no kernel component validates
or consumes them:

```json
{
  "schema_version": "zf.decision_map.v1",
  "work_id": "cangjie-agent-plan",
  "source_refs": ["docs/research/openclaw.md", "docs/research/hermes.md"],
  "knowns": [
    {
      "id": "K-001",
      "statement": "The product requires a GA core plus vertical data-agent capabilities.",
      "source_refs": ["docs/research/product-baseline.md"]
    }
  ],
  "unknowns": [
    {
      "id": "U-001",
      "question": "Which plugin boundary is stable enough for phase 1?",
      "owner_hint": "arch|research|operator",
      "resolution_path": "research|prototype|user_decision"
    }
  ],
  "decisions": [
    {
      "id": "D-001",
      "decision": "Use a thin kernel and skill-driven artifact contracts first.",
      "rationale": "Keeps runtime deterministic while improving agent output quality.",
      "source_refs": ["docs/design/62-plan-artifact-adapter-task-map-synthesis.md"]
    }
  ],
  "rejected_options": [
    {
      "id": "R-001",
      "option": "Put all claim reasoning into kernel enforcement immediately.",
      "reason": "Too thick for the current kernel boundary.",
      "source_refs": ["docs/design/23-kanban-runtime-projection-boundary.md"]
    }
  ],
  "blockers": [
    {
      "id": "B-001",
      "description": "Need source freshness before finalizing competitive assumptions.",
      "owner_hint": "research",
      "severity": "low|medium|high|blocking"
    }
  ],
  "probes": [
    {
      "id": "P-001",
      "question": "Can the current task-map be split into independent vertical slices?",
      "expected_evidence": ["task_map.json", "source_index.json"]
    }
  ],
  "replan_triggers": [
    {
      "trigger": "critical unknown resolved differently than assumed",
      "expected_action": "file a goal-gap-plan.v1 amendment via the canonical replan channel"
    }
  ]
}
```

Canonical replan channel: a decision map explains a replan; it does not
execute one. The productized path is a `goal-gap-plan.v1` artifact plus the
`task_map.amend.requested` / `task_map.amended` / `task_map.amend.failed`
events (see `zf-goal-closure-replan-contract`). Fingerprint dedupe applies to
the resulting plan (see Fail Closed): a replan that keeps the same task set
while a plan is pending is suppressed via `plan.minting.suppressed`. Tier-2
diagnosis already machine-generates part of this "why the plan changed"
evidence — a `diagnosis.completed` report with `next_action: "route_to_lane"`
flows back into the lane as replan feedback — so link that report in
`source_refs` instead of re-deriving the rationale by hand.

Use with the task map:

- before task-map synthesis: record decisions and unknowns;
- during replan: explain what changed, and reference the amendment artifact;
- include `decision_map_ref` in plan/replan summaries when available;
- keep dependencies and dispatch constraints in `task_map.json`.
