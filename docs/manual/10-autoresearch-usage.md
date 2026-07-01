# Autoresearch 使用手册

> 适用日期: 2026-05-26 UTC
> 来源: 当前工作区 `uv run zf autoresearch --help`、`uv run zf watch --help`

本文是 Autoresearch 的总入口。更窄的操作说明见
[autoresearch-orchestrator.md](autoresearch-orchestrator.md) 和
[autoresearch-campaign.md](autoresearch-campaign.md)。

## 1. Autoresearch 是什么

Autoresearch 是跑在被测 harness 外层的评估/自演进监督器。它不替代内层
ZaoFu runtime,而是用真实或可控场景反复验证这些能力:

- multi-agent / multi-replica 是否真的完成多个 task。
- long-horizon 任务是否能通过 arch、critic、dev、review、test、judge、
  discriminator 等路径收敛。
- stuck worker、rework、manual intervention、terminal evidence 等边界是否
  fail-closed。
- 失败是否能沉淀为可修复 backlog / trigger / self-repair 记录。

核心边界:

- 外层 Autoresearch 负责准备 worktree、启动内层 harness、汇总报告。
- 内层 ZaoFu 仍然通过 `zf.yaml`、`events.jsonl`、`kanban.json` 等运行。
- 观察运行态时优先使用 `zf watch`、`zf events`、`zf status --workers`、
  `zf kanban --board`。
- 真实 provider run 会消耗预算,加 `--confirm` 前先确认 worktree、配置和预算。

## 2. 命令地图

| 命令 | 用途 |
|---|---|
| `uv run zf autoresearch run` | 跑单个内置 scenario,生成 run 报告 |
| `uv run zf autoresearch loop` | 多轮 scenario / bypass 循环,每轮评估、反思、等待修复 |
| `uv run zf autoresearch campaign plan` | 生成多 scenario campaign 计划和脚本 |
| `uv run zf autoresearch discover-bugs` | 从 run 或 state 中提取 failure signals / bug candidates |
| `uv run zf autoresearch triggers scan` | 按 trigger policy 扫描是否应启动 autoresearch/self-repair |
| `uv run zf autoresearch self-repair prepare` | 进入维护/修复准备状态 |
| `uv run zf autoresearch self-repair checkpoint` | 记录修复过程 checkpoint |
| `uv run zf autoresearch self-repair validate` | 标记某次 repair run 的验证结果 |

常用观察命令:

| 命令 | 用途 |
|---|---|
| `uv run zf watch --follow --state-dir "$WT/.zf"` | tail 被测 worktree 的事件流 |
| `uv run zf watch --type worker.stuck --follow --state-dir "$WT/.zf"` | 只看 stuck 事件 |
| `uv run zf watch --task TASK-XXX --follow --state-dir "$WT/.zf"` | 只看某个 task |
| `uv run zf events --last 50 --state-dir "$WT/.zf"` | 查看最近事件 |
| `uv run zf status --workers --state-dir "$WT/.zf"` | 查看 worker 状态 |
| `uv run zf kanban --board --state-dir "$WT/.zf"` | 查看被测看板 |
| `uv run zf task trace TASK-XXX --state-dir "$WT/.zf"` | 查看 task 因果链 |

## 3. 场景选择

当前内置 scenario:

| Scenario | 目标 | 默认 done | 默认 timeout |
|---|---|---:|---:|
| `self-eval-backlog` | 真实 self-eval backlog bridge hardening | 4 | 10800s |
| `positive-pressure-4dev` | 四个独立任务压测 dev/test replica 和完整 gates | 4 | 10800s |
| `controlled-stuck-recovery` | 验证 stuck/requeue/recovery 链路 | 1 | 7200s |
| `fail-rework-converge` | 制造一次失败并验证 bounded rework 收敛 | 1 | 7200s |
| `manual-intervention-guard` | 验证人工/Web 干预不能静默改 canonical done | 1 | 5400s |
| `spec-validate-hardening` | 针对 `zf spec validate` 的 schema hardening | 2 | 10800s |

选择建议:

- 想先验证 recovery: 跑 `controlled-stuck-recovery`。
- 想验证多 worker 吞吐: 跑 `positive-pressure-4dev`。
- 想验证 rework 收敛: 跑 `fail-rework-converge`。
- 想验证 operator guard: 跑 `manual-intervention-guard`。
- 想验证 spec/任务提示词入口: 跑 `spec-validate-hardening`。
- 想做默认自评估闭环: 跑 `self-eval-backlog`。

## 4. 单场景 run

先 dry-run。没有 `--confirm` 时不会启动真实 provider:

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

需要验证 strict/full Codex DAG 时,使用
`examples/zf-full-codex-autoresearch.yaml`。它是
`examples/zf-full-codex.yaml` 的 Autoresearch-safe 变体:保留 full DAG、
all-Codex roles、strict skills 和 gate 配置,但 `project.state_dir` 与
`runtime.workdirs.root`、`runtime.skills.pool`、`runtime.skills.lock_file` 都
落在 `.zf/**`,避免 `autoresearch run` 复制模板后同时生成 `.zf` 与
`.zf-full-codex` 两套 runtime state tree。

```bash
uv run zf autoresearch run \
  --scenario controlled-stuck-recovery \
  --worktree /tmp/zf-ar-full-codex-template-dry \
  --config examples/zf-full-codex-autoresearch.yaml \
  --expected-done 1 \
  --timeout 7200 \
  --budget-usd 260
```

未加 `--confirm` 时这是 dry-run,会生成 Autoresearch run report,不会启动真实
provider 长跑。

真实运行:

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

推荐 `--tmux`:外层 supervisor 会启动独立 tmux session,并提供事件/status
观察窗口。输出中会出现:

```text
Autoresearch supervisor started in tmux session: zf-ar-supervisor-<run-id>
Attach with: tmux attach -t zf-ar-supervisor-<run-id>
```

常用参数:

| 参数 | 说明 |
|---|---|
| `--scenario NAME` | 内置 scenario 名 |
| `--worktree PATH` | 被测隔离 worktree |
| `--config PATH` | 复制到被测 worktree 的 `zf.yaml` 模板 |
| `--seed-file PATH` | 覆盖 scenario 默认 seed |
| `--expected-done N` | 期望完成 task 数 |
| `--timeout SECONDS` | 内层 runner 超时 |
| `--budget-usd N` | 写入被测配置的预算 |
| `--reuse-worktree` | 复用已有 worktree |
| `--keep-running` | 内层 runner 结束后不自动 stop |
| `--backlog-on-failure` | 失败时写入 backlog |
| `--backlog-state-dir PATH` | 指定失败 backlog 写入位置 |
| `--inject-worker-stuck` | 外层在真实 dispatch 后注入 stuck 事件 |
| `--no-sync-dirty` | 只评估 HEAD,不 overlay 当前未提交改动 |
| `--confirm` | 真正执行真实 provider run |
| `--tmux` | 启动外层 tmux supervisor |

## 5. 运行中观察

如果用了 `--tmux`,先 attach 外层 supervisor:

```bash
tmux attach -t zf-ar-supervisor-<run-id>
```

直接观察内层 worktree:

```bash
uv run zf watch --follow --state-dir "$WT/.zf"
uv run zf status --workers --state-dir "$WT/.zf"
uv run zf kanban --board --state-dir "$WT/.zf"
```

按事件类型过滤:

```bash
uv run zf watch --type task.dispatched --follow --state-dir "$WT/.zf"
uv run zf watch --type worker.stuck --follow --state-dir "$WT/.zf"
uv run zf watch --type task.done.blocked --follow --state-dir "$WT/.zf"
uv run zf watch --type discriminator.failed --follow --state-dir "$WT/.zf"
```

按 task 过滤:

```bash
uv run zf watch --task TASK-ABCDEF --follow --state-dir "$WT/.zf"
uv run zf task trace TASK-ABCDEF --state-dir "$WT/.zf"
```

排查卡住时先看:

```bash
uv run zf status --workers --state-dir "$WT/.zf"
uv run zf events --last 80 --state-dir "$WT/.zf"
uv run zf backlog why-not-done TASK-ABCDEF --state-dir "$WT/.zf"
```

重点关注:

- `task.status_changed` 到 `done` 的数量是否达到 `expected_done`。
- 是否出现 `orchestrator.dispatch_failed`、`task.invalid_transition`、
  `worker.stuck.recovery_failed`、`cost.budget.exceeded` 等 fatal signals。
- `task.dispatched` 是否分布到多个 dev/test replica。
- 是否存在同一 stage 反复 `rework` 但没有新 evidence。
- done task 是否有 terminal evidence。

## 6. 输出产物

单场景 run 产物默认位于:

```text
$WT/.zf/autoresearch/runs/<run-id>/
```

核心文件:

| 文件 | 含义 |
|---|---|
| `scenario.json` | scenario、worktree、seed、timeout、budget manifest |
| `inner-runner.log` | 内层 runner stdout/stderr/exit code |
| `events-summary.json` | done 数、fatal event、dispatch 分布、derived metrics |
| `iterations.tsv` | 可聚合趋势行 |
| `report.md` | 人类阅读报告 |

建议阅读顺序:

1. `report.md`: 先看 pass/fail 和失败摘要。
2. `events-summary.json`: 看 `derived_metrics`。
3. `inner-runner.log`: 查 runner / provider / tmux 级别错误。
4. `zf watch` / `zf task trace`: 回到事件流定位因果链。

关键 metrics:

| 指标 | 签收含义 |
|---|---|
| `fatal_count` | 必须为 0 |
| `tasks_done` | 应大于等于 `expected_done` |
| `duplicate_success_event_count` | 必须为 0 |
| `terminal_evidence_coverage` | done task 应覆盖 terminal evidence |
| `task_done_blocked_count` | 出现时要确认是预期 guard 还是真实失败 |
| `rework_signal_count` | rework 场景中应可解释,非 rework 场景要警惕 |
| `dev_replicas_used` / `test_replicas_used` | 多 replica 场景应符合预期 |
| `stuck_injection_satisfied` | stuck 场景应为 true |

## 7. Stuck Recovery 验证

`controlled-stuck-recovery` 建议打开 deterministic stuck 注入:

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

观察:

```bash
uv run zf watch --type autoresearch.inject.worker_stuck --follow --state-dir "$WT/.zf"
uv run zf watch --type worker.stuck --follow --state-dir "$WT/.zf"
uv run zf watch --type worker.stuck.recovered --follow --state-dir "$WT/.zf"
```

签收:

- `stuck_injection_requested_count >= 1`
- `worker_stuck_count >= 1`
- `worker_stuck_recovered_count >= 1`
- `worker_stuck_recovery_failed_count == 0`
- `stuck_injection_satisfied == true`

## 8. Loop 模式

`autoresearch loop` 用于多轮验证:每轮跑一个 scenario,收集指标,生成
`iter-NNN.md`,可选调用 reflection backend,然后等待修复后进入下一轮。

基本命令:

```bash
uv run zf autoresearch loop \
  --scenarios controlled-stuck-recovery positive-pressure-4dev \
  --worktree /tmp/zf-ar-loop \
  --max-iterations 4 \
  --budget-usd 500 \
  --config examples/dev-codex-backends.yaml \
  --fix-wait-strategy head_change
```

常用参数:

| 参数 | 说明 |
|---|---|
| `--scenarios A B` | 按顺序轮转 scenario |
| `--worktree PATH` | 循环使用的被测 worktree |
| `--parent-state-dir PATH` | 父 ZaoFu state dir |
| `--max-iterations N` | 最大轮次 |
| `--output-dir PATH` | `journal.jsonl`、`iter-NNN.md`、`report.md` 输出目录 |
| `--reflect-backend BACKEND` | 用于反思的 LLM backend；支持 `claude-code` / `codex` |
| `--fix-wait-strategy head_change|manual|none` | 每轮后等待修复的方式 |
| `--screenshot-url URL` | 每轮用 Docker Playwright 截图 Web |
| `--bypass-autoresearch` | 不用内置 scenario scaffold,直接驱动自定义 yaml/seed |

`--reflect-backend` 显式传参优先；未传时读取
`ZF_AUTORESEARCH_REFLECT_BACKEND`，仍未设置则默认 `claude-code`。例如:

```bash
ZF_AUTORESEARCH_REFLECT_BACKEND=codex \
uv run zf autoresearch loop \
  --scenarios controlled-stuck-recovery \
  --worktree /tmp/zf-ar-loop
```

输出:

```text
<output-dir>/journal.jsonl
<output-dir>/iter-001.md
<output-dir>/iter-002.md
<output-dir>/report.md
```

### Bypass 模式

当你想用某个真实项目的 `zf.yaml` 和一段自定义任务 seed 进行循环验证时:

```bash
uv run zf autoresearch loop \
  --scenarios bypass \
  --worktree /tmp/zf-ar-cangjie \
  --max-iterations 3 \
  --bypass-autoresearch \
  --yaml-template /path/to/project/zf.yaml \
  --seed-text "实现一个小功能并完成 review/test/judge" \
  --expected-done 1 \
  --inner-wait-timeout 7200 \
  --fix-wait-strategy manual
```

Bypass 每轮会清理 `.zf`,复制 yaml,执行 `zf init` / `zf start`,发
`user.message`,等待 terminal done,再 `zf stop`。

## 9. Campaign 计划

生成 campaign plan:

```bash
uv run zf autoresearch campaign plan \
  --campaign harness-hardening \
  --output-dir /tmp/zf-ar-campaign-plan \
  --worktree-root /tmp/zf-ar-campaign \
  --config examples/dev-codex-backends.yaml
```

输出:

| 文件 | 用途 |
|---|---|
| `campaign.json` | 机器可读场景计划 |
| `campaign.md` | 人类阅读签收说明 |
| `run-campaign.sh` | 顺序执行脚本 |

先逐个跑 scenario,不要一开始直接全量脚本。推荐顺序:

1. `controlled-stuck-recovery`
2. `positive-pressure-4dev`
3. `fail-rework-converge`
4. `manual-intervention-guard`

## 10. Failure Signals、Triggers 与 Self-Repair

从已有 run 中提取 bug candidates:

```bash
uv run zf autoresearch discover-bugs \
  --run-dir "$WT/.zf/autoresearch/runs/<run-id>" \
  --out /tmp/zf-ar-bugs.json \
  --campaign harness-hardening
```

扫描是否触发 autoresearch/self-repair:

```bash
uv run zf autoresearch triggers scan \
  --state-dir .zf \
  --severity-min high \
  --cooldown-minutes 60 \
  --max-triggers-per-hour 2 \
  --max-daily-runs 4
```

如果不传 `--severity-min` / `--cooldown-minutes` /
`--max-triggers-per-hour` / `--max-daily-runs`，CLI 会读取 `zf.yaml`:

```yaml
autoresearch:
  trigger_policy:
    enabled: true
    mode: continuous
    severity_min: high
    cooldown_minutes: 30
    max_triggers_per_hour: 5000
    max_daily_runs: 5000
```

`mode: continuous` 会让 `zf start` 的 supervisor tick 周期扫描 failure
signals，并只把 accepted trigger 写入 `events.jsonl`；`manual` /
`supervised` 不会自动写入，但仍可用 CLI 手动扫描和 `--write-events`。

需要把 trigger 决策写入 events 时加:

```bash
--write-events
```

Self-repair 辅助:

```bash
uv run zf autoresearch self-repair prepare \
  --trigger TRIGGER-ID \
  --reason "autoresearch failure requires maintenance"

uv run zf autoresearch self-repair checkpoint \
  --task TASK-ABCDEF \
  --role dev \
  --worker dev-1 \
  --progress "patched failure signal classifier" \
  --stage implementation

uv run zf autoresearch self-repair validate \
  --repair-run REPAIR-RUN-ID \
  --summary "controlled-stuck-recovery passed" \
  --passed
```

这些命令用于维护审计和恢复上下文,不是直接绕过 gates。

## 11. 常见工作流

### 单场景冒烟

```bash
STAMP="$(date -u +%Y%m%d%H%M%S)"
WT="/tmp/zf-ar-smoke-${STAMP}"

uv run zf autoresearch run \
  --scenario controlled-stuck-recovery \
  --worktree "$WT" \
  --config examples/dev-codex-backends.yaml \
  --expected-done 1 \
  --timeout 7200 \
  --budget-usd 180 \
  --inject-worker-stuck \
  --backlog-on-failure \
  --tmux \
  --confirm
```

另开窗口:

```bash
uv run zf watch --follow --state-dir "$WT/.zf"
```

### 失败后定位

```bash
RUN_DIR="$WT/.zf/autoresearch/runs/<run-id>"

sed -n '1,220p' "$RUN_DIR/report.md"
jq '.derived_metrics, .fatal_event, .dispatch_by_instance' "$RUN_DIR/events-summary.json"
uv run zf events --last 100 --state-dir "$WT/.zf"
```

如果知道 task:

```bash
uv run zf task trace TASK-ABCDEF --state-dir "$WT/.zf"
uv run zf backlog why-not-done TASK-ABCDEF --state-dir "$WT/.zf"
```

### 修复后回归

```bash
uv run zf autoresearch run \
  --scenario <failed-scenario> \
  --worktree /tmp/zf-ar-regression \
  --config examples/dev-codex-backends.yaml \
  --backlog-on-failure \
  --tmux \
  --confirm
```

## 12. 清理

停止内层 harness:

```bash
uv run zf stop --force --state-dir "$WT/.zf" 2>/dev/null || true
```

清理 tmux:

```bash
tmux kill-session -t "zf-autoresearch-<run-id>" 2>/dev/null || true
tmux kill-session -t "zf-ar-supervisor-<run-id>" 2>/dev/null || true
```

临时 worktree 建议使用 `/tmp/zf-<purpose>-<utc-timestamp>/`。保留
`report.md` 和 `events-summary.json` 后再删除临时目录。

## 13. 签收口径

一次 Autoresearch 不能只看命令退出码。最小签收:

- `report.md` 存在,并说明 pass/fail。
- `events-summary.json` 存在。
- `fatal_count == 0`。
- `tasks_done >= expected_done`。
- `duplicate_success_event_count == 0`。
- terminal done 有 evidence。
- stuck 场景 `stuck_injection_satisfied == true`。
- 多 replica 场景的 `dev_replicas_used` / `test_replicas_used` 符合预期。
- 若失败,已通过 `discover-bugs`、`backlog-on-failure` 或人工 backlog 沉淀修复项。
