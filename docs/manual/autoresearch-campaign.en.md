# Autoresearch Campaign

> Audience: operators running several Autoresearch metric scenarios as one
> long-horizon acceptance batch.

## 1. Purpose

`autoresearch campaign` does not start providers. It generates:

- `campaign.json`: machine-readable scenarios, metrics, assertions, and commands.
- `campaign.md`: human-readable acceptance instructions.
- `run-campaign.sh`: a sequential runner.

Real execution still uses `zf autoresearch run`, with an isolated worktree and
state directory for every scenario.

## 2. Generate a Plan

```bash
cd /path/to/zaofu
PYTHONPATH="$(pwd)/src" python3 -m zf.cli.main autoresearch campaign plan \
  --campaign full-validation \
  --output-dir /tmp/zaofu-ar-campaign-plan \
  --worktree-root /tmp/zaofu-ar-campaign \
  --config examples/dev-codex-backends.yaml
```

Add `--no-tmux` when the generated commands should not use an outer supervisor.

## 3. Built-In Campaigns

`full-validation` plans all six scenarios:

| Scenario | Goal |
|---|---|
| `controlled-stuck-recovery` | Verify stuck, requeue, and recovery first |
| `positive-pressure-4dev` | Exercise replicas, handoff, and terminal evidence |
| `fail-rework-converge` | Verify bounded convergence after a fail-closed result |
| `manual-intervention-guard` | Prove external intervention cannot bypass truth |
| `self-eval-backlog` | Verify self-eval backlog, no-op pass, repair contract, and docs evidence |
| `spec-validate-hardening` | Verify literal verification and reference-scope hardening |

`harness-hardening` is the smaller four-scenario spine and omits the last two.

## 4. Execution Order

### Phase 0: Provider-Free Preflight

```bash
uv run zf validate --path examples/dev-codex-backends.yaml
PYTHONPATH="$(pwd)/src" uv run pytest -q tests/test_autoresearch_campaign.py
uv run zf autoresearch campaign plan \
  --campaign full-validation \
  --output-dir /tmp/zf-ar-full-validation-campaign-plan \
  --worktree-root /tmp/zf-ar-full-validation-wt \
  --config examples/dev-codex-backends.yaml \
  --no-tmux
```

### Phase 1: One-Scenario Smoke

Run only `controlled-stuck-recovery` with explicit time and budget. Do not
expand the campaign if injection and recovery fail.

### Phase 2: Sequential Expansion

After the smoke passes, run the six scenarios in `campaign.json` order. Give
each scenario a fresh worktree and state directory; never continue expansion in
a failed run's worktree.

## 5. Budget, Failure, and Cleanup

- Use each scenario's `budget_usd` and `timeout_seconds` with `--confirm`.
- Budget exhaustion or timeout is a failure, not a pass.
- Use `--backlog-on-failure` for every real run.
- Preserve `report.md`, `events-summary.json`, and `inner-runner.log` before cleanup.
- Stop the harness in its worktree and remove only its named tmux sessions.
- Repair and rerun a failed scenario alone before continuing the campaign.

## 6. Metrics

Review fatal counts, stuck injection and recovery counts, blocked terminal
completion, done-evidence coverage, discriminator results, invalid transitions,
duplicate success events, rework signals, and actual dev/test replica use.

## 7. Acceptance

Every scenario needs a report and event summary, enough done tasks, zero fatal
and duplicate-success counts, and terminal evidence for every done task. A stuck
scenario additionally requires a requested injection, an observed stuck event,
successful recovery, and no recovery-failed event. Stop at the first failure and
turn its evidence into repair work.
