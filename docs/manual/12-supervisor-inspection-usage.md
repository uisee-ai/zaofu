# Supervisor Inspection 使用手册

> 适用对象: 需要观察 long-horizon / multi-agent 运行健康度、确认是否需要
> Autoresearch / maintenance / Project Spine Review 介入的 ZaoFu 操作者。
>
> 状态: **v0**(只读巡检层 — 生成 supervisor projection 并给出建议,自动执行
> 恢复动作的能力仍有限,边界见文末"当前限制")。

## 1. 当前定位

Supervisor Inspection 是一个轻量、只读、可重建的在线巡检层。它读取
`events.jsonl`、`kanban.json`、`role_sessions.yaml`、Autoresearch failure
signals、Automation projection、Pause Lifecycle projection 和 Project Spine
Review insight,生成一个 supervisor projection。

它不是新的控制面:

- 不替代 `zf.yaml`。
- 不直接派发任务。
- 不直接修改 `kanban.json` / `feature_list.json` / `session.yaml`。
- 不直接 kill worker 或恢复 worker。
- v0 不 emit `runtime.attention.*`。
- v0 不在每轮 tick 自动运行 Project Spine Review。

当前 v0 的主要产物在:

```text
<state_dir>/projections/supervisor/snapshot.json
<state_dir>/projections/supervisor/attention-candidates.json
<state_dir>/projections/supervisor/plan-integrity.json
<state_dir>/projections/supervisor/snapshot.sha256
```

默认 `state_dir` 是 `zf.yaml` 里的 `project.state_dir`,通常为 `.zf/`。

## 2. 适用场景

优先使用 Supervisor Inspection 观察这些问题:

| 场景 | 观察目标 |
|---|---|
| 长时间无进展 | task / worker / event freshness 是否异常 |
| worker stuck / silent | role session、stuck event、pause lifecycle 是否一致 |
| Autoresearch 候选 | failure signals 是否已经出现,是否需要重型验证 |
| 多入口告警分散 | Autopilot proposal、Automation alerts、Plan Integrity drift 归并为 attention candidates |
| 计划与执行漂移 | task contract 是否缺 plan/spec/backlog refs,evidence 是否缺失 |
| maintenance 前检查 | 当前是否已 pause,是否有 checkpoint/resume 线索 |
| Project Spine Review 前置输入 | spine review 是否能读取 supervisor snapshot 作为 runtime context |

不适合用 Supervisor v0 做这些事:

- 自动修复 zaofu bug。
- 自动停止业务任务。
- 自动创建 bug backlog。
- 自动把 high severity attention 转成 spine review artifact/proposal。
- 代替 Autoresearch replay / eval。

这些属于后续 P2/P3 能力或 Autoresearch 本身。

## 3. 自动刷新

真实 `zf start` watcher 运行时,Supervisor projection 会在 tick 中按节流刷新,
当前间隔约 300 秒。

```bash
uv run zf start
```

或者在已经启动的项目中观察 projection:

```bash
STATE_DIR="$(uv run python - <<'PY'
from pathlib import Path
from zf.core.config.loader import load_config
cfg = load_config(Path("zf.yaml"))
print(Path(cfg.project.state_dir))
PY
)"

ls "$STATE_DIR/projections/supervisor"
jq '.attention_summary' "$STATE_DIR/projections/supervisor/snapshot.json"
```

注意: `zf start --dry-run` 只 poll 一次 watcher,不等价于长期在线巡检。

## 4. 手动刷新

当前还没有 `zf supervisor` CLI。需要手动刷新时,可以直接调用 runtime API:

```bash
uv run python - <<'PY'
from pathlib import Path
from zf.core.config.loader import load_config
from zf.runtime.supervisor_inspection import run_supervisor_inspection

project_root = Path.cwd()
config = load_config(project_root / "zf.yaml")
state_dir = project_root / config.project.state_dir
result = run_supervisor_inspection(
    state_dir,
    config=config,
    project_root=project_root,
)
print(result["snapshot_path"])
print("changed:", result["changed"])
print("hash:", result["hash"])
PY
```

`changed=false` 表示去掉 `generated_at` 和 age 类字段后,核心 projection 没有变化,
因此不会重复写入 snapshot 文件。

## 5. 读取 Snapshot

常用字段:

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

关键字段含义:

| 字段 | 含义 |
|---|---|
| `task_summary` | task 总数、active 数、状态分布、blocked 数、带 plan ref 的 active 数 |
| `worker_summary` | role session worker 状态、heartbeat age、stuck event 数 |
| `freshness` | 最近事件类型、事件 id、事件时间和 age |
| `pause_lifecycle` | 当前是否处于 maintenance/pause/resume 相关状态 |
| `failure_signals` | Autoresearch failure signal 的只读摘要 |
| `autoresearch_triggers` | 最近 Autoresearch trigger decision |
| `attention_items` | 归并后的候选注意力项 |
| `attention_summary` | attention 按 source / severity 聚合 |
| `plan_integrity` | task/backlog/design/evidence drift 只读投影 |
| `spine_review_hint` | 最近 Project Spine Review insight 的轻量提示 |

## 6. Attention Candidates

`attention-candidates.json` 是 v0 的统一告警候选入口。

```bash
jq '.summary' .zf/projections/supervisor/attention-candidates.json
jq '.items[] | {source,severity,title,task_id,suggested_route,source_ref}' \
  .zf/projections/supervisor/attention-candidates.json
```

当前来源包括:

| source | 输入来源 | 常见 suggested_route |
|---|---|---|
| `autopilot` | `autopilot.proposal.created` | `l2_orchestrator` |
| `automation` | `project_automations()` 的 monitor alerts / open proposals | `l2_orchestrator` |
| `autoresearch` | `collect_failure_signals()` | `autoresearch_trigger` |
| `plan_integrity` | `plan-integrity.json` findings | `plan_revision` |

v0 只生成 candidates,不自动唤醒 L2,也不自动执行 suggested route。

`autoresearch` 来源中的 `handoff_stall` 只表示“一个成功事件老化后仍没有可观察到
的后续推进”。`static_gate.passed` / `static_gate.skipped` 刚出现后的短暂调度窗口
不会立刻升级为 Autoresearch；如果同一 task 后续已经出现 `task.dispatched`、
`review.*`、`test.*`、`judge.*`、`discriminator.*`、`fanout.*` 或
`task.done.evidence` 等进展，也会被视为已恢复，避免成功链路完成后仍生成
`static_gate.passed did not hand off ...` 的误报。

## 7. Plan Integrity

`plan-integrity.json` 用来观察“需求/设计/任务/证据是否断链”。

```bash
jq '.summary' .zf/projections/supervisor/plan-integrity.json
jq '.findings[] | {kind,severity,title,task_id,source_ref,suggested_route}' \
  .zf/projections/supervisor/plan-integrity.json
```

当前 v0 检查:

| kind | 含义 |
|---|---|
| `task-missing-plan-ref` | active task 缺少 `plan_ref` / `spec_ref` / `source_backlog_task_id` 等引用 |
| `acceptance-without-evidence` | 有 `acceptance_criteria` 但没有 `acceptance_evidence` |
| `weak-acceptance` | acceptance 文本缺少 verify/check/evidence 语义 |
| `doc-acceptance-without-verify` | `tasks/` 或 `backlogs/` 文档提到验收但缺少 `verify:` 或 `-> verify` |

这不是第二套 task schema。它只读现有 task contract 和本地 task/backlog 文档。

## 8. Maintenance Prepare

当 operator 判断 zaofu harness bug 已经影响执行面,可以通过 Web controlled action
进入 maintenance prepare。它是 token-gated action path,会复用:

- `enter_maintenance()`
- `dispatch.paused`
- 可选 `create_checkpoint()`
- `runtime.action.completed`
- `web.action.completed`

先启动 Web:

```bash
export ZF_WEB_ACTION_TOKEN="$(openssl rand -hex 16)"
uv run zf web --host 127.0.0.1 --port 8002
```

另一个终端调用:

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

带 checkpoint 的调用需要 `task_id`:

```bash
curl -sS \
  -H "x-zf-web-token: $ZF_WEB_ACTION_TOKEN" \
  -H "content-type: application/json" \
  -X POST \
  http://127.0.0.1:8002/api/actions/maintenance.prepare \
  -d '{
    "trigger_id": "manual-supervisor-check",
    "reason": "pause before zaofu self-repair",
    "task_id": "TASK-123",
    "checkpoint": true,
    "role": "dev",
    "assigned_worker": "dev-1",
    "last_progress": "before maintenance"
  }' | jq
```

检查事件:

```bash
uv run zf events --last 20
```

应看到:

```text
web.action.requested
runtime.action.accepted
runtime.maintenance.entered
dispatch.paused
worker.checkpointed        # 仅 checkpoint=true 且 task_id 存在时
runtime.action.completed
web.action.completed
```

## 9. Project Spine Review 联动

Project Spine Review 会读取 supervisor snapshot,但 Supervisor v0 不主动运行
spine review。推荐先刷新 supervisor,再手动运行 spine review 相关命令或 API。

用 Python 验证 spine 是否读到 snapshot:

```bash
uv run python - <<'PY'
from pathlib import Path
from zf.runtime.project_spine_review import (
    build_project_spine_review,
    resolve_spine_review_context,
)

context = resolve_spine_review_context(project_root=Path.cwd())
review = build_project_spine_review(context)
supervisor = review["runtime_spine"]["supervisor_snapshot"]
print(supervisor.get("status"))
print(supervisor.get("attention_summary", {}))
print(supervisor.get("plan_integrity_summary", {}))
PY
```

期望 `status` 为 `ready`。如果是 `empty`,说明还没有生成
`projections/supervisor/snapshot.json`。

## 10. 最小 E2E 验证

用于验证当前实现是否打通:

```bash
tmp="/tmp/zf-supervisor-e2e-$(date -u +%Y%m%d-%H%M%S)"
mkdir -p "$tmp"
cd "$tmp"

cat > zf.yaml <<'YAML'
version: "1.0"
project:
  name: supervisor-e2e
  state_dir: .zf
session:
  tmux_session: zf-supervisor-e2e
orchestrator:
  backend: mock
roles:
  - name: dev
    backend: mock
YAML

uv run --project /path/to/zaofu zf init --no-workspace-register

uv run --project /path/to/zaofu python - <<'PY'
from pathlib import Path
from zf.core.config.loader import load_config
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.task.schema import Task, TaskContract
from zf.core.task.store import TaskStore
from zf.runtime.supervisor_inspection import run_supervisor_inspection
from zf.runtime.project_spine_review import build_project_spine_review, resolve_spine_review_context

root = Path.cwd()
config = load_config(root / "zf.yaml")
state_dir = root / config.project.state_dir
TaskStore(state_dir / "kanban.json").add(Task(
    id="TASK-SUP",
    title="supervisor e2e",
    status="in_progress",
    contract=TaskContract(acceptance="manual approval"),
))
EventLog(state_dir / "events.jsonl").append(ZfEvent(
    type="autopilot.proposal.created",
    actor="autopilot",
    task_id="TASK-SUP",
    payload={"dedupe_key": "e2e", "severity": "high", "title": "e2e attention"},
))
result = run_supervisor_inspection(state_dir, config=config, project_root=root)
review = build_project_spine_review(resolve_spine_review_context(project_root=root))
assert result["changed"] is True
assert (state_dir / "projections" / "supervisor" / "snapshot.json").exists()
assert review["runtime_spine"]["supervisor_snapshot"]["status"] == "ready"
print("supervisor e2e passed")
PY
```

再验证 Web action:

```bash
export ZF_WEB_ACTION_TOKEN="supervisor-e2e-token"
uv run --project /path/to/zaofu zf web --host 127.0.0.1 --port 8002 &
web_pid=$!
sleep 2

curl -sS \
  -H "x-zf-web-token: $ZF_WEB_ACTION_TOKEN" \
  -H "content-type: application/json" \
  -X POST \
  http://127.0.0.1:8002/api/actions/maintenance.prepare \
  -d '{"trigger_id":"e2e","reason":"supervisor e2e"}' | jq

kill "$web_pid"
```

检查:

```bash
grep -E 'runtime.maintenance.entered|dispatch.paused|web.action.completed' \
  .zf/events.jsonl
```

## 11. 常见问题

### 没有 `projections/supervisor/`

原因通常是 watcher 没有跑到 tick,或还没手动调用 `run_supervisor_inspection()`。
先执行“手动刷新”命令。

### `changed=false`

这是正常结果。表示核心 snapshot 没变化,不会因为 `generated_at` 或 age 字段导致
projection churn。

### attention 很多,但任务没有自动暂停

这是 v0 预期。v0 只生成 candidates。自动路由到 L2 / Autoresearch / Spine
Review / maintenance 仍是后续阶段。

### `maintenance.prepare` 返回 403

Web mutation 没启用或 token 不匹配。确认:

```bash
echo "$ZF_WEB_ACTION_TOKEN"
```

请求必须带:

```text
x-zf-web-token: <same token>
```

### `maintenance.prepare` 带 checkpoint 返回 422

`checkpoint=true` 时必须传 `task_id`。没有 task id 时只能进入 maintenance,
不能创建 worker checkpoint。

### spine review 里 supervisor status 是 `empty`

先生成 supervisor snapshot:

```bash
uv run python - <<'PY'
from pathlib import Path
from zf.core.config.loader import load_config
from zf.runtime.supervisor_inspection import run_supervisor_inspection
root = Path.cwd()
config = load_config(root / "zf.yaml")
run_supervisor_inspection(root / config.project.state_dir, config=config, project_root=root)
PY
```

## 12. 当前限制

- 没有 `zf supervisor` CLI。
- 没有 Web 页面专门展示 supervisor queue。
- 没有 `runtime.attention.*` event 化。
- 没有 attention ack / snooze / feedback。
- 没有 high severity 自动触发 Project Spine Review artifact/proposal。
- 没有自动恢复 maintenance 后的业务任务 checkpoint。
- 没有 supervisor meta metrics / false positive feedback loop。

这些限制是刻意保守的。v0 目标是先把观察面打通,不引入第二控制面。
