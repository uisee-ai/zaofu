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

## Product Boundary

ZaoFu plan stages have two output layers:

- Human layer: durable markdown for review and discussion.
- Machine layer: `task_map.json` referenced by `task_map_ref`.

Customer or project skills may influence domain judgment, but the machine
contract is fixed. Do not rely on markdown-only task lists for dispatch.

## Required Artifacts

Write durable artifacts before emitting the stage success event:

- `plan_artifact_ref`: markdown plan, normally under `docs/plans/` or the
  fanout artifact directory.
- `task_map_ref`: JSON task map consumed by writer fanout.
- `source_index_ref`: JSON or markdown evidence/source index when source facts
  were inspected.

Include these paths in `artifact_refs` / `evidence_refs` as appropriate.

## Source Provenance Contract

Every dispatchable task must be traceable back to the plan, scan, PRD, issue,
or research evidence that caused it. Strict/release workflows fail closed when
per-task provenance is missing.

Use at least one of these accepted task-level fields on each task:

- `source_key`: one stable anchor string.
- `source_keys`: one or more stable anchor strings.
- `source_ref`: one file/section reference.
- `source_refs`: one or more file/section references.
- `source_excerpt`: a short evidence excerpt tied to the task.

If the task itself is intentionally compact, `source_index.json` must map every
`task_id` through `tasks[]` or `task_sources[]`:

```json
{
  "schema_version": "source-index.v1",
  "tasks": [
    {
      "task_id": "PDD-CORE-001",
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
  "task_map_ref": ".zf/artifacts/example/task_map.json",
  "source_index_ref": ".zf/artifacts/example/source_index.json",
  "artifact_refs": [
    "docs/plans/example-plan.md",
    ".zf/artifacts/example/task_map.json"
  ],
  "evidence_refs": [
    ".zf/artifacts/example/source_index.json"
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
    "source_index_ref": ".zf/artifacts/example/source_index.json"
  },
  "tasks": [
    {
      "task_id": "PDD-CORE-001",
      "title": "Implement one vertical slice",
      "summary": "Concrete behavior and scope.",
      "owner_role": "dev-core",
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

Every task must be small enough for one focused worker, have explicit ownership,
list dispatch-safe paths, and include verification evidence. If a task touches
workspace scaffolding such as `package.json`, `pyproject.toml`, `setup.py`,
`setup.cfg`, `tsconfig.json`, lockfiles, or root build config, it must own
those paths explicitly. For greenfield products under a subdirectory, treat that
subdirectory's package scaffold (for example `app/pyproject.toml`) as the
project scaffold owner and list it in both `allowed_paths` and
`shared_conventions.packaging_file`.

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
