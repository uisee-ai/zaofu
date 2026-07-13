# Product Fanout Real E2E

> Audience: maintainers and QA validating a complete product-fanout workflow
> against a real Codex provider. This is not a production operations guide. For
> scheduling concepts, see
> [Plan, Task Map, and Orchestrator Dispatch](13-plan-task-map-orchestrator-dispatch.en.md).

This procedure validates `examples/workflow-product-fanout-standard-codex.yaml`
through:

```text
user.message -> plan -> impl -> verify -> judge
```

## 1. When to Run It

Run after changing fanout readers or writers, candidate integration, verify or
judge dispatch, standard workflow YAML defaults, or fixes involving event
schema, `candidate.ready`, checkout, branch, and worktree conflicts.

## 2. Prepare an Isolated Worktree

```bash
stamp="$(date -u +%Y%m%d-%H%M%S)"
run_dir="/tmp/zf-product-fanout-e2e-${stamp}"
git worktree add --detach "$run_dir" HEAD
cd "$run_dir"
cp /path/to/zaofu/examples/workflow-product-fanout-standard-codex.yaml zf.yaml
```

Use unique names for every run:

```bash
export ZF_BRANCH_PREFIX="zf-product-fanout-e2e-${stamp}"
export ZF_TMUX_SESSION="zf-product-fanout-e2e-${stamp}"
export ZF_STATE_DIR=".zf"
```

## 3. Start

`zf start` resolves `zf.yaml` from the current project; it has no `--path`
option:

```bash
uv run zf validate --path zf.yaml
uv run zf start
```

From another terminal in the same `run_dir`, submit real work:

```bash
uv run zf emit user.message --payload '{
  "message": "Build a minimal product fanout E2E artifact: create docs/product-fanout-e2e.md with scope, implementation notes, and verification evidence.",
  "pdd_id": "PDD-PRODUCT-FANOUT-E2E",
  "feature_id": "PDD-PRODUCT-FANOUT-E2E"
}'
```

## 4. Observe

The event chain should include:

```text
product.plan.ready
product.design.ready
task_map.ready
dev.build.done
candidate.ready
test.passed
judge.passed
```

Summarize counts:

```bash
python - <<'PY'
import json
from collections import Counter

events = [json.loads(line) for line in open(".zf/events.jsonl", encoding="utf-8")]
counts = Counter(event["type"] for event in events)
for key in [
    "fanout.started",
    "fanout.child.dispatched",
    "fanout.aggregate.completed",
    "product.plan.ready",
    "product.design.ready",
    "task_map.ready",
    "candidate.ready",
    "test.passed",
    "test.failed",
    "judge.passed",
    "event.schema.violated",
]:
    print(f"{key}: {counts.get(key, 0)}")
PY
```

## 5. Acceptance

- At least one `judge.passed` exists.
- No blocking `test.failed` or `judge.failed` exists.
- `candidate.ready` contains candidate ref, base commit, head commit, diff ref,
  and completed task IDs.
- `event.schema.violated` is zero, unless a specific warning is explicitly allowed and explained.
- Verify and judge use `${candidate_ref}` as `target_ref`, not `main` or `HEAD`.

## 6. Cleanup

Stop from the tested project directory, because `zf stop` also has no `--path`
option:

```bash
cd "$run_dir"
uv run zf stop || true
tmux kill-session -t "$ZF_TMUX_SESSION" 2>/dev/null || true

cd /path/to/zaofu
git worktree remove --force "$run_dir" 2>/dev/null || true
git worktree prune
for br in $(git branch --format='%(refname:short)' | grep "^${ZF_BRANCH_PREFIX}/"); do
  git branch -D "$br"
done
rm -rf "$run_dir"
```

Confirm that only this run's tmux session and worktree are gone. Never use a
global tmux kill command.
