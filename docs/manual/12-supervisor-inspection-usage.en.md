# Supervisor Inspection

> Audience: operators assessing long-horizon and multi-agent runtime health and
> deciding whether Autoresearch, maintenance, or Project Spine Review should
> intervene.
>
> Status: implemented projection and bounded signaling, not a replacement for
> `zf.yaml` or kernel truth.

## 1. Role

Supervisor Inspection is a lightweight rebuildable online inspection layer. It
reads events, Kanban, role sessions, Autoresearch failure signals, Automation,
pause lifecycle, and Project Spine Review insight. It writes:

```text
<state_dir>/projections/supervisor/snapshot.json
<state_dir>/projections/supervisor/attention-candidates.json
<state_dir>/projections/supervisor/plan-integrity.json
<state_dir>/projections/supervisor/snapshot.sha256
```

Its bounded control loop may record a decision, owner-visible message, or
`autoresearch.invocation.requested` through `EventWriter`. It does not dispatch
tasks, edit truth files, kill or recover workers, apply repairs, or run Project
Spine Review on every tick.

## 2. Use Cases

Use it to inspect stale progress, silent workers, Autoresearch candidates,
dispersed alerts, plan-to-execution drift, maintenance readiness, and runtime
context for a spine review. Do not use it as an automatic bug fixer, task
stopper, backlog creator, automatic spine-review applier, or replacement for
Autoresearch replay and evaluation.

## 3. Automatic Refresh

During a real `zf start` watcher, projections refresh on a throttled tick,
currently around 300 seconds:

```bash
uv run zf start
ls .zf/projections/supervisor
jq '.attention_summary' .zf/projections/supervisor/snapshot.json
```

Use the configured `project.state_dir` when it differs from `.zf`. A dry-run
start polls once and is not persistent inspection.

## 4. Manual Refresh

There is no `zf supervisor` CLI yet. Call the runtime API:

```bash
uv run python - <<'PY'
from pathlib import Path
from zf.core.config.loader import load_config
from zf.runtime.supervisor_inspection import run_supervisor_inspection

root = Path.cwd()
config = load_config(root / "zf.yaml")
result = run_supervisor_inspection(
    root / config.project.state_dir,
    config=config,
    project_root=root,
)
print(result["snapshot_path"])
print("changed:", result["changed"])
print("hash:", result["hash"])
PY
```

`changed=false` means the semantic projection is unchanged after excluding
volatile timestamp and age fields, so no duplicate snapshot is written.

## 5. Read the Snapshot

```bash
jq '{
  schema_version,
  generated_at,
  project_id,
  task_summary,
  worker_summary,
  freshness,
  pause_lifecycle,
  attention_summary,
  source_projections
}' .zf/projections/supervisor/snapshot.json
```

`task_summary` reports task states and plan-reference coverage;
`worker_summary` reports worker status, heartbeat age, and stuck events;
`freshness` identifies the newest event; `pause_lifecycle` reports maintenance
state; `failure_signals` and `autoresearch_triggers` summarize diagnosis;
`attention_items` aggregates candidates; `plan_integrity` reports drift; and
`spine_review_hint` summarizes the latest spine insight.

## 6. Attention Candidates

```bash
jq '.summary' .zf/projections/supervisor/attention-candidates.json
jq '.items[] | {source,severity,title,task_id,suggested_route,source_ref}' \
  .zf/projections/supervisor/attention-candidates.json
```

Sources include Autopilot proposals, Automation alerts, Autoresearch failure
signals, and plan-integrity findings. Suggested routes include L2 Orchestrator,
Autoresearch trigger, and plan revision. Candidates do not themselves pause or
repair work.

A `handoff_stall` means an aged success event has no visible continuation. A
short post-gate dispatch window is ignored, and later dispatch, review, test,
judge, discriminator, fanout, or done-evidence activity clears the condition.

## 7. Plan Integrity

```bash
jq '.summary' .zf/projections/supervisor/plan-integrity.json
jq '.findings[] | {kind,severity,title,task_id,source_ref,suggested_route}' \
  .zf/projections/supervisor/plan-integrity.json
```

Current findings include active tasks missing plan/spec/backlog provenance,
acceptance criteria without evidence, weak acceptance language, and task or
backlog documents that mention acceptance without an executable `verify:` or
`-> verify` clause. This is a read-only projection over the existing contract,
not a second task schema.

## 8. Maintenance Prepare

When a harness bug affects execution, an operator may request maintenance via a
token-gated controlled action. It enters maintenance, pauses dispatch, and can
create a task checkpoint.

```bash
export ZF_WEB_ACTION_TOKEN="$(openssl rand -hex 16)"
uv run zf web --host 127.0.0.1 --port 8002
```

From another terminal:

```bash
curl -sS \
  -H "x-zf-web-token: $ZF_WEB_ACTION_TOKEN" \
  -H "content-type: application/json" \
  -X POST \
  http://127.0.0.1:8002/api/actions/maintenance.prepare \
  -d '{
    "trigger_id": "manual-supervisor-check",
    "reason": "operator requested supervised maintenance"
  }' | jq
```

For a checkpoint, include `checkpoint: true`, `task_id`, role, assigned worker,
and last progress. The audit chain should include Web request, runtime accept,
maintenance entry, dispatch pause, optional worker checkpoint, and runtime/Web
completion.

## 9. Project Spine Review

Supervisor does not launch a spine review, but a review can consume the latest
snapshot. Refresh Supervisor first. A ready integration reports
`runtime_spine.supervisor_snapshot.status == "ready"`; `empty` means no snapshot
has been generated.

## 10. Minimal E2E

Create a temporary project with a mock role, initialize it, add an in-progress
task and one high-severity proposal event through `TaskStore` and `EventLog`, run
`run_supervisor_inspection()`, and assert:

- `changed` is true on first generation;
- `projections/supervisor/snapshot.json` exists;
- Project Spine Review sees Supervisor status `ready`.

Then start Web on a temporary port with `ZF_WEB_ACTION_TOKEN`, POST
`maintenance.prepare`, and verify `runtime.maintenance.entered`,
`dispatch.paused`, and `web.action.completed` in `events.jsonl`.

## 11. Troubleshooting

- Missing projection directory: the watcher has not reached a tick; run manual refresh.
- `changed=false`: normal when semantic content has not changed.
- Many attention items but no pause: expected; candidates require a bounded decision or operator action.
- HTTP 403 from maintenance: action token is missing or mismatched.
- HTTP 422 with checkpoint: `checkpoint=true` requires `task_id`.
- Spine status `empty`: generate a Supervisor snapshot first.

## 12. Current Limits

There is no dedicated CLI or Supervisor queue page, attention acknowledgment or
snooze, automatic high-severity spine-review artifact, automatic business-task
resume after maintenance, or false-positive feedback loop. These conservative
limits keep inspection and controlled signaling from becoming a second control
plane.
