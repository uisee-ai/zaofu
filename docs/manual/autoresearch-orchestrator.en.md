# Autoresearch Orchestrator

> Audience: operators evaluating ZaoFu or Yoke long-horizon behavior with real
> harness scenarios and turning failures into actionable backlog work.

This manual describes the outer deterministic supervisor, not the inner harness.

## 1. Boundary

The Autoresearch Orchestrator:

1. Prepares an isolated Git worktree.
2. Generates its `zf.yaml` from a template.
3. Writes a real scenario seed.
4. Starts the inner harness runner.
5. Aggregates events, done counts, fatal events, and dispatch distribution.
6. Writes an evaluation report.
7. Optionally upserts a failed run into a Kanban backlog.
8. Optionally injects one audited stuck-worker recovery test after real dispatch.

Implementation is under `src/zf/autoresearch/`; CLI wiring is in
`src/zf/cli/autoresearch.py`; the default runner is `tests.e2e.run_mixed`; and
the default template is `examples/dev-codex-backends.yaml`.

## 2. Prerequisites

Real runs start tmux workers and provider CLIs. Confirm Git, tmux, Python, and
provider login; ensure the repository can create worktrees; validate backend
configuration; and set an explicit budget before adding `--confirm`.

## 3. Dry Run

```bash
cd /path/to/zaofu
PYTHONPATH="$(pwd)/src" python3 -m zf.cli.main autoresearch run \
  --scenario self-eval-backlog \
  --worktree /tmp/zaofu-autoresearch-dry
```

A dry run validates scenario parsing, command shape, and report paths without
starting provider CLIs. Output should identify the run directory and report.

## 4. Recommended Real Run

```bash
cd /path/to/zaofu
STAMP="$(date -u +%Y%m%d%H%M%S)"
WT="/tmp/zaofu-autoresearch-${STAMP}"

PYTHONPATH="$(pwd)/src" python3 -m zf.cli.main autoresearch run \
  --scenario self-eval-backlog \
  --worktree "$WT" \
  --config examples/dev-codex-backends.yaml \
  --expected-done 4 \
  --timeout 10800 \
  --budget-usd 500 \
  --backlog-on-failure \
  --tmux \
  --confirm
```

Attach the outer `zf-ar-supervisor-<run-id>` session to observe aggregation, or
the inner `zf-autoresearch-<run-id>` session to inspect the harness directly.

## 5. Observe Execution

The outer supervisor normally has `supervisor`, `events`, and `status` windows.
From another terminal:

```bash
tail -f "$WT/.zf/events.jsonl"
(cd "$WT" && PYTHONPATH=/path/to/zaofu/src python3 -m zf.cli.main status --workers)
(cd "$WT" && PYTHONPATH=/path/to/zaofu/src python3 -m zf.cli.main kanban --board)
```

Track done counts, fatal events, actual multi-worker distribution, repeated
stalls, and budget. Fatal types include dispatch failure, invalid transition,
budget exhaustion, failed run or ship, orphaned task, failed respawn/recycle,
and failed stuck recovery.

## 6. Output

Each run writes `scenario.json`, `inner-runner.log`, `events-summary.json`,
`iterations.tsv`, and `report.md` under:

```text
$WT/.zf/autoresearch/runs/<run-id>/
```

Read the report first, then metrics and dispatch details in the event summary,
then the inner log.

## 7. Backlog on Failure

`--backlog-on-failure` upserts a failed run into the tested worktree's Kanban
state. Use `--backlog-state-dir /path/to/.zf` to target another controlled state
directory. Stable keys prevent duplicate tasks for the same failure class;
events use actor `zf-autoresearch` and source `autoresearch`.

## 8. Important Options

| Option | Meaning |
|---|---|
| `--scenario` | Built-in scenario name |
| `--worktree` | Isolated worktree; required |
| `--config` | YAML template |
| `--expected-done` | Required completed task count |
| `--timeout` | Inner runner timeout |
| `--budget-usd` | Global budget written into test config |
| `--reuse-worktree` | Reuse an existing worktree |
| `--keep-running` | Do not stop the inner harness at runner exit |
| `--runner-module` | Override the inner runner |
| `--run-id` / `--output-dir` | Stabilize run identity and artifacts |
| `--inject-worker-stuck` | Request deterministic stuck injection |
| `--inject-worker-stuck-instance` | Target instance or role |
| `--inject-worker-stuck-timeout` | Waiting-warning interval for target dispatch |
| `--tmux` | Start the outer supervisor |
| `--confirm` | Execute real provider work |

## 9. Deterministic Stuck Injection

Use explicit injection for `controlled-stuck-recovery`. The outer supervisor
waits for the target's real `task.dispatched`, emits
`autoresearch.inject.worker_stuck`, and lets the inner runtime execute its normal
stuck, requeue, recover, and redispatch path. The timeout records waiting but
does not close the injection window; failure is decided when the inner run exits
without satisfying the injection.

Acceptance requires an injection request, an observed stuck and recovery, and
no recovery-failed event.

## 10. Cleanup

```bash
(cd "$WT" && PYTHONPATH=/path/to/zaofu/src python3 -m zf.cli.main stop) \
  2>/dev/null || true
tmux kill-session -t "zf-autoresearch-<run-id>" 2>/dev/null || true
tmux kill-session -t "zf-ar-supervisor-<run-id>" 2>/dev/null || true

cd /path/to/zaofu
git worktree remove "$WT" --force
```

Stop and remove only resources belonging to this run.

## 11. Acceptance

Require inner exit code zero, enough done transitions, no fatal event, all core
artifacts present, real multi-worker distribution where expected, and an
actionable backlog with report and reproduction command when the run fails.
The `self-eval-backlog` scenario is designed to expose implementation defects in
the harness, orchestration, verification, and backlog closure, not to establish
statistical significance.
