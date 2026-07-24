# Delivery Trace (`zf trace`)

> Audience: operators tracing how a feature was planned, executed, and possibly
> diverted from its accepted task map.

## 1. Purpose

Delivery trace commands are read-only projections. They join an accepted
`task-map.v1` with current Kanban tasks and events to answer:

- How was a feature split, and how are dependencies and waves ordered?
- Which worker ran each task, what is its state, and what evidence exists?
- Did execution drift in dependency, assignment, scope, or evidence?
- What prevents the feature from being ship-ready?

Trace does not write runtime state or rejudge kernel decisions. For example, it
surfaces an existing `scope.violation`; it does not rerun that gate.

## 2. Commands

| Command | Purpose | Schema |
|---|---|---|
| `zf trace delivery <feature_id>` | Delivery spine, waves, drift, and readiness | `delivery-trace.v1` |
| `zf trace execution-graph <feature_id>` | Planned and actual task graph | `execution-graph.v1` |
| `zf trace drift <feature_id>` | Planned-versus-actual drift | `drift-report.v1` |
| `zf trace task-node <task_id>` | One task's planned and actual node | execution-graph node |
| `zf trace workflow-run <fanout_id>` | Fanout/workflow run trace | workflow run projection |
| `zf trace report <feature_id>` | Generate a trace report | report artifact |
| `zf trace export <feature_id> --format otlp-json` | Export span telemetry | OTLP JSON |
| `zf trace export --run-id <run_id> --format completion-json` | Export a kernel-admitted Goal completion receipt | `goal-completion-receipt.v1` |

Delivery query options include `--format table|json`, `--task-map-ref <path>`,
and `--state-dir <path>`. Without an explicit task-map path, the resolver
checks `<state_dir>/artifacts/<feature_id>/task_map.json`.

`trace export` instead accepts `--format otlp-json|completion-json` and
`--output <path>|-`; `completion-json` additionally requires `--run-id`.

## 3. Examples

```bash
zf trace delivery F-CANGJIE-GA
zf trace execution-graph F-CANGJIE-GA
zf trace drift F-CANGJIE-GA
zf trace task-node TASK-API-002
zf trace workflow-run fanout-123
zf trace report F-CANGJIE-GA
zf trace delivery F-CANGJIE-GA --format json
zf trace export F-CANGJIE-GA --format otlp-json
zf trace export --run-id run-20260724-001 --format completion-json
```

Overall `status` can be `in_progress`, `done`, `blocked`, `not_started`, or
`empty`. Ship readiness is `ready` only when every node is done and no
error-level drift exists. It is still only a projection; actual shipping
requires the ship gate or operator action.

`completion-json` is stricter than the normal trace status. It succeeds only
when the explicit run has one current, evidence-complete `run.goal.completed`.
Missing or duplicate completion events, missing Verify/Goal Closure/Candidate/
delivery references, or a later state change cause a non-zero exit. A task or
span marked `completed` is never promoted into Goal completion.

The receipt is a redacted, read-only EventLog projection rather than new
canonical truth. Its `source_fingerprint` detects input drift; without a
separate signature layer it is not a cryptographic attestation across trust
boundaries.

## 4. Drift Classes

| Type | Trigger | Severity |
|---|---|---|
| `dependency_drift` | A task starts or finishes before a dependency is done | error |
| `assignment_drift` | Actual assignee role differs from planned `owner_role` | warning |
| `evidence_drift` | A done task has no recorded evidence event | warning |
| `scope_drift` | The kernel emitted `scope.violation` for the task | warning |

The projection consumes kernel decisions; it does not create a second decision
surface.

## 5. Degraded Modes

- Without an accepted task map, trace builds a Kanban-only graph and reports `task_map_missing`.
- Legacy tasks without `feature_id` enter a synthetic trace and are not mixed with a real feature.
- A planned node missing from Kanban has `actual.status = not_created` and `kanban_task_missing`.
- A Kanban task absent from the map reports `task_not_in_task_map`.

## 6. Implementation and API

Projection logic lives in `src/zf/runtime/execution_graph.py`,
`delivery_trace.py`, `drift_report.py`, and `goal_completion_receipt.py`.
Loading is in
`delivery_trace_resolve.py`; Web routes are in `delivery_trace_routes.py`; CLI
entry points are in `src/zf/cli/trace.py`.

Important API routes include:

| Route | Purpose |
|---|---|
| `/api/projects/{project_id}/delivery-traces/{feature_id}` | Basic trace |
| `/api/projects/{project_id}/delivery-traces/{feature_id}/thick` | Thick trace, cursors, and deltas |
| `/api/projects/{project_id}/delivery-traces/{feature_id}/causation/{event_id}` | Event causation |
| `/api/projects/{project_id}/delivery-traces/{feature_id}/execution-graph` | Execution graph |
| `/api/projects/{project_id}/delivery-traces/{feature_id}/drift-report` | Drift report |
| `/api/projects/{project_id}/workflow-runs/{fanout_id}` | Fanout/workflow run |

The Loop page links bug-fix, reflection, replan, and A/B loops to delivery
traces.

## 7. Principle

`task-map.v1` says how work should run. `zf trace delivery` reports whether it
ran that way. Corrections still go through Orchestrator, rework, or remapping.
