# Autoresearch

> Verified against the local CLI on 2026-07-07 UTC.

This is the primary Autoresearch manual. See
[Autoresearch Orchestrator](autoresearch-orchestrator.en.md) and
[Autoresearch Campaign](autoresearch-campaign.en.md) for narrower procedures.

## 1. What Autoresearch Is

Autoresearch is an outer evaluation and self-improvement supervisor around a
harness under test. It does not replace the inner ZaoFu runtime. It repeatedly
tests whether:

- multiple agents and replicas complete multiple tasks;
- long-horizon work converges through architecture, critique, development,
  review, test, judge, and discriminator stages;
- stuck workers, rework, manual intervention, and terminal evidence fail closed;
- failures become actionable backlogs, triggers, or self-repair records.

The outer layer prepares worktrees, starts the inner harness, and aggregates
reports. The inner run still uses `zf.yaml`, `events.jsonl`, `kanban.json`, and
normal kernel behavior. Real providers consume budget. Resident and repair
paths require explicit authorization; the default posture is proposal-only,
sandbox-first, and no automatic mainline apply.

## 2. Command Map

| Command | Purpose |
|---|---|
| `uv run zf autoresearch run` | Run one built-in scenario |
| `uv run zf autoresearch loop` | Run multi-round scenario or bypass loops |
| `uv run zf autoresearch campaign plan` | Generate a multi-scenario campaign |
| `uv run zf autoresearch discover-bugs` | Extract failure signals and bug candidates |
| `uv run zf autoresearch triggers scan` | Evaluate trigger policy |
| `uv run zf autoresearch review-gate` | Check a repair or closeout before human approval |
| `uv run zf autoresearch compare` | Compare baseline and candidate runs |
| `uv run zf autoresearch export-eval-result` | Export evaluation results |
| `uv run zf autoresearch resident` | Start the opt-in resident consumer |
| `uv run zf autoresearch self-repair prepare` | Enter repair preparation |
| `uv run zf autoresearch self-repair checkpoint` | Record repair progress |
| `uv run zf autoresearch self-repair validate` | Record repair validation |

Runtime ticks may scan triggers, and Supervisor or Run Manager may emit
`autoresearch.invocation.requested`. These are auditable diagnostic requests,
not automatic mainline repair. `resident` is not started by default with
`zf start`; execution requires additional authorization; closeout still uses a
human apply gate.

Useful observation commands:

```bash
uv run zf watch --follow --state-dir "$WT/.zf"
uv run zf watch --type worker.stuck --follow --state-dir "$WT/.zf"
uv run zf status --workers --state-dir "$WT/.zf"
uv run zf kanban --board --state-dir "$WT/.zf"
uv run zf task trace TASK-XXX --state-dir "$WT/.zf"
```

## 3. Choose a Scenario

| Scenario | Goal | Default done | Timeout |
|---|---|---:|---:|
| `self-eval-backlog` | Harden self-eval to backlog closure | 4 | 10800s |
| `positive-pressure-4dev` | Exercise four independent tasks and replicas | 4 | 10800s |
| `controlled-stuck-recovery` | Verify stuck, requeue, and recovery | 1 | 7200s |
| `fail-rework-converge` | Cause one failure and verify bounded convergence | 1 | 7200s |
| `manual-intervention-guard` | Prove external intervention cannot forge done | 1 | 5400s |
| `spec-validate-hardening` | Harden structured spec validation | 2 | 10800s |

Choose the scenario matching the behavior under test. Use
`controlled-stuck-recovery` as the first recovery smoke and
`positive-pressure-4dev` for multi-worker throughput.

## 4. Run One Scenario

Start without `--confirm`; this prepares and reports but does not launch a real
provider:

```bash
cd /path/to/zaofu
STAMP="$(date -u +%Y%m%d%H%M%S)"
WT="/tmp/zf-autoresearch-${STAMP}"

uv run zf autoresearch run \
  --scenario controlled-stuck-recovery \
  --worktree "$WT" \
  --config examples/dev-codex-backends.yaml \
  --expected-done 1 \
  --timeout 7200 \
  --budget-usd 180
```

For the strict full-Codex DAG, use the Autoresearch-safe template. It keeps
runtime paths under one `.zf` tree:

```bash
uv run zf autoresearch run \
  --scenario controlled-stuck-recovery \
  --worktree /tmp/zf-ar-full-codex-template-dry \
  --config examples/zf-full-codex-autoresearch.yaml \
  --expected-done 1 \
  --timeout 7200 \
  --budget-usd 260
```

Launch the real run only after confirming the worktree, provider, timeout, and
budget:

```bash
uv run zf autoresearch run \
  --scenario controlled-stuck-recovery \
  --worktree "$WT" \
  --config examples/dev-codex-backends.yaml \
  --expected-done 1 \
  --timeout 7200 \
  --budget-usd 180 \
  --backlog-on-failure \
  --tmux \
  --confirm
```

`--tmux` starts an outer `zf-ar-supervisor-<run-id>` session. Other important
options are `--seed-file`, `--reuse-worktree`, `--keep-running`,
`--backlog-state-dir`, `--inject-worker-stuck`, and `--no-sync-dirty`.

## 5. Observe a Run

Attach the outer supervisor or inspect the inner state directly:

```bash
tmux attach -t zf-ar-supervisor-<run-id>
uv run zf watch --follow --state-dir "$WT/.zf"
uv run zf status --workers --state-dir "$WT/.zf"
uv run zf kanban --board --state-dir "$WT/.zf"
```

Filter key events:

```bash
uv run zf watch --type task.dispatched --follow --state-dir "$WT/.zf"
uv run zf watch --type worker.stuck --follow --state-dir "$WT/.zf"
uv run zf watch --type task.done.blocked --follow --state-dir "$WT/.zf"
uv run zf watch --type discriminator.failed --follow --state-dir "$WT/.zf"
```

Watch done counts, fatal signals, replica distribution, repeated rework without
new evidence, terminal-evidence coverage, and budget use. Fatal signals include
dispatch failure, invalid transition, recovery failure, and budget exhaustion.

## 6. Output Artifacts

Each run writes:

```text
$WT/.zf/autoresearch/runs/<run-id>/
```

| File | Meaning |
|---|---|
| `scenario.json` | Scenario, worktree, seed, timeout, and budget manifest |
| `inner-runner.log` | Inner stdout, stderr, and exit code |
| `events-summary.json` | Done count, fatal events, dispatch distribution, and metrics |
| `iterations.tsv` | Aggregatable trend row |
| `report.md` | Human-readable result |

Read `report.md`, then `derived_metrics` in `events-summary.json`, then the inner
log and event trace. Key acceptance metrics are zero fatal and duplicate-success
counts, enough done tasks, complete terminal evidence, expected replica usage,
and a satisfied stuck injection when applicable.

## 7. Verify Stuck Recovery

```bash
uv run zf autoresearch run \
  --scenario controlled-stuck-recovery \
  --worktree "$WT" \
  --config examples/dev-codex-backends.yaml \
  --expected-done 1 \
  --timeout 7200 \
  --budget-usd 180 \
  --inject-worker-stuck \
  --inject-worker-stuck-instance dev-1 \
  --backlog-on-failure \
  --tmux \
  --confirm
```

The run passes this dimension only when injection was requested, at least one
stuck and recovered event exists, no recovery-failed event exists, and
`stuck_injection_satisfied` is true.

## 8. Loop Mode

`autoresearch loop` runs several rounds, evaluates each, writes an iteration
report, optionally asks a reflection backend, and waits for a fix:

```bash
uv run zf autoresearch loop \
  --scenarios controlled-stuck-recovery positive-pressure-4dev \
  --worktree /tmp/zf-ar-loop \
  --max-iterations 4 \
  --budget-usd 500 \
  --config examples/dev-codex-backends.yaml \
  --fix-wait-strategy head_change
```

The reflection backend may be `claude-code` or `codex`; an explicit option wins,
then `ZF_AUTORESEARCH_REFLECT_BACKEND`, then `claude-code`. Output includes
`journal.jsonl`, `iter-NNN.md`, and `report.md`.

For a real project's YAML and custom seed, use bypass mode:

```bash
uv run zf autoresearch loop \
  --scenarios bypass \
  --worktree /tmp/zf-ar-project \
  --max-iterations 3 \
  --bypass-autoresearch \
  --yaml-template /path/to/project/zf.yaml \
  --seed-text "Implement a small feature through review, test, and judge" \
  --expected-done 1 \
  --inner-wait-timeout 7200 \
  --fix-wait-strategy manual
```

Each bypass round cleans `.zf`, copies YAML, initializes, starts, emits a user
message, waits for terminal completion, and stops.

## 9. Campaign Planning

```bash
uv run zf autoresearch campaign plan \
  --campaign harness-hardening \
  --output-dir /tmp/zf-ar-campaign-plan \
  --worktree-root /tmp/zf-ar-campaign \
  --config examples/dev-codex-backends.yaml
```

This writes `campaign.json`, `campaign.md`, and `run-campaign.sh`. Run scenarios
one by one, beginning with stuck recovery, rather than launching the full script
before the smoke passes.

## 10. Failure Signals, Triggers, and Self-Repair

```bash
uv run zf autoresearch discover-bugs \
  --run-dir "$WT/.zf/autoresearch/runs/<run-id>" \
  --out /tmp/zf-ar-bugs.json \
  --campaign harness-hardening

uv run zf autoresearch triggers scan \
  --state-dir .zf \
  --severity-min high \
  --cooldown-minutes 60 \
  --max-triggers-per-hour 2 \
  --max-daily-runs 4
```

Without explicit policy options, trigger scan reads `zf.yaml`. Continuous mode
allows the Supervisor tick to scan failure signals and append accepted trigger
decisions. Manual and supervised modes remain operator-driven. Add
`--write-events` when the CLI decision should be recorded.

Repair audit commands are:

```bash
uv run zf autoresearch self-repair prepare \
  --trigger TRIGGER-ID \
  --reason "autoresearch failure requires maintenance"

uv run zf autoresearch self-repair checkpoint \
  --task TASK-ABCDEF \
  --role dev \
  --worker dev-1 \
  --progress "patched failure classifier" \
  --stage implementation

uv run zf autoresearch self-repair validate \
  --repair-run REPAIR-RUN-ID \
  --summary "controlled-stuck-recovery passed" \
  --passed
```

These commands preserve maintenance and recovery context. They do not bypass
review, verification, or the human closeout gate.

## 11. Failure Diagnosis and Regression

```bash
RUN_DIR="$WT/.zf/autoresearch/runs/<run-id>"
sed -n '1,220p' "$RUN_DIR/report.md"
jq '.derived_metrics, .fatal_event, .dispatch_by_instance' \
  "$RUN_DIR/events-summary.json"
uv run zf events --last 100 --state-dir "$WT/.zf"
```

For a known task, use `task trace` and `backlog why-not-done`. After repair,
rerun the failed scenario alone in a fresh temporary worktree before expanding
the test surface.

## 12. Cleanup

Stop from the tested worktree so `zf stop` resolves its `zf.yaml` and state:

```bash
(cd "$WT" && uv run zf stop --force) 2>/dev/null || true
tmux kill-session -t "zf-autoresearch-<run-id>" 2>/dev/null || true
tmux kill-session -t "zf-ar-supervisor-<run-id>" 2>/dev/null || true
```

Preserve `report.md` and `events-summary.json` before deleting the temporary
worktree. Do not kill unrelated tmux sessions.

## 13. Acceptance

A run is not accepted by exit code alone. Require a pass/fail report and event
summary, zero fatal and duplicate-success counts, enough done tasks, terminal
evidence for done tasks, satisfied recovery injection where requested, expected
replica use, and an actionable backlog or bug candidate for every failure.
