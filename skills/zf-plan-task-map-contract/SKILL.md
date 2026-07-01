---
name: zf-plan-task-map-contract
description: "Use in ZaoFu plan/task-map synthesis roles. Defines the fixed machine contract that downstream writer fanout, Kanban, verify, and judge stages consume. Customer/domain skills may be added, but they do not replace this contract."
---

# ZaoFu Plan Task-Map Contract

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
  "pdd_id": "<stable product/work id>",
  "feature_id": "<stable feature id>",
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
      "acceptance": ["Behavior is implemented"],
      "verification": ["uv run pytest tests/example/test_service.py"],
      "source_keys": ["docs/plans/example-plan.md#slice-core"],
      "evidence_contract": ["test output", "changed file summary"]
    }
  ]
}
```

Every task must be small enough for one focused worker, have explicit ownership,
list dispatch-safe paths, and include verification evidence. If a task touches
workspace scaffolding such as `package.json`, `tsconfig.json`, lockfiles, or
root build config, it must own those paths explicitly.

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
  all bundles).
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

## Fail Closed

Do not emit `task_map.ready` if `task_map_ref` is missing, unreadable, or points
to markdown. Emit the configured failure event with a concrete reason instead.

Do not emit `task_map.ready` in strict/release workflows when any task lacks a
task-level source anchor and no `source_index_ref` maps that task id to anchors.
