# Product Fanout 真实 E2E 手册

> 定位: 面向维护者 / QA 的**验证手册**,用于确认 product fanout workflow 在真实
> Codex provider 下能跑通完整链路;不是日常生产运行指南。想在自己项目里理解
> fanout 调度,见 [13-plan-task-map-orchestrator-dispatch.md](13-plan-task-map-orchestrator-dispatch.md)。

本手册用于验证 `examples/workflow-product-fanout-standard-codex.yaml` 是否能在真实 Codex provider 下跑完整产品交付链路:

```text
user.message -> plan -> impl -> verify -> judge
```

## 适用场景

- 调整 fanout reader / writer / candidate integration / verify / judge 调度后。
- 调整标准 workflow YAML 默认值后。
- 修复 `event.schema.violated`、`candidate.ready`、checkout、branch/worktree 冲突后。

## 准备

使用临时目录,不要污染真实项目状态:

```bash
stamp="$(date -u +%Y%m%d-%H%M%S)"
run_dir="/tmp/zf-product-fanout-e2e-${stamp}"
git worktree add --detach "$run_dir" HEAD
cd "$run_dir"
cp /path/to/zaofu/examples/workflow-product-fanout-standard-codex.yaml zf.yaml
```

每轮必须使用唯一分支前缀:

```bash
export ZF_BRANCH_PREFIX="zf-product-fanout-e2e-${stamp}"
export ZF_TMUX_SESSION="zf-product-fanout-e2e-${stamp}"
export ZF_STATE_DIR=".zf"
```

## 启动

```bash
uv run zf validate --path zf.yaml
uv run zf start
```

触发真实任务:

```bash
uv run zf emit user.message --payload '{
  "message": "Build a minimal product fanout E2E artifact: create docs/product-fanout-e2e.md with scope, implementation notes, and verification evidence.",
  "pdd_id": "PDD-PRODUCT-FANOUT-E2E",
  "feature_id": "PDD-PRODUCT-FANOUT-E2E"
}'
```

## 观察

事件链至少应包含:

```text
product.plan.ready
product.design.ready
task_map.ready
dev.build.done
candidate.ready
test.passed
judge.passed
```

摘要命令:

```bash
python - <<'PY'
import json
from collections import Counter
events = [json.loads(line) for line in open(".zf/events.jsonl", encoding="utf-8")]
counts = Counter(e["type"] for e in events)
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

## 通过标准

- `judge.passed >= 1`。
- 没有阻塞级 `test.failed` / `judge.failed`。
- `candidate.ready` payload 包含 `candidate_ref`、`candidate_base_commit`、`candidate_head_commit`、`diff_ref`、`completed_task_ids`。
- `event.schema.violated == 0`; 若存在,必须能解释为明确允许的 warning。
- verify / judge 的 `target_ref` 来自 `${candidate_ref}`,不是 `main` 或 `HEAD`。

## 清理

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

清理后确认:

```bash
tmux list-sessions 2>/dev/null | grep "$ZF_TMUX_SESSION" || true
git worktree list | grep "$run_dir" || true
```
