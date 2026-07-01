# Autoresearch Orchestrator 使用手册

> 适用对象: 用真实 harness 场景评估 ZaoFu / Yoke 的 long-horizon 能力,并在失败时沉淀可修复 backlog 的操作者。
> 本手册描述的是外层 deterministic supervisor,不是内层被测 harness 本身。

## 1. 作用边界

`autoresearch orchestrator` 是跑在被测 harness 外面的监督器:

1. 准备隔离 `git worktree`。
2. 从模板生成被测 worktree 的 `zf.yaml`。
3. 写入真实场景 seed。
4. 启动内层 harness runner。
5. 汇总 `.zf/events.jsonl`、done 数、fatal event、worker dispatch 分布。
6. 生成本次评估报告。
7. 可选: 失败时把问题写回 Kanban backlog。
8. 可选: 在真实派发后注入一次 audited `worker.stuck` 恢复验证。

当前实现入口:

- 代码目录: `src/zf/autoresearch/`
- CLI: `src/zf/cli/autoresearch.py`
- 默认内层 runner: `tests.e2e.run_mixed`
- 默认配置模板: `examples/dev-codex-backends.yaml`

## 2. 前置条件

真实运行会启动 tmux workers 并调用 provider CLI,会消耗真实预算。运行前确认:

- 当前仓库在 `/path/to/zaofu`。
- `git`、`tmux`、`python3` 可用。
- Codex/Claude 等 provider CLI 已登录,且 `examples/dev-codex-backends.yaml` 中的 backend 配置可用。
- 当前 repo 可以创建 `git worktree`。
- 预算上限已明确,不要在未确认预算时加 `--confirm` 真跑。

建议先跑 dry-run,再真跑。

## 3. Dry Run

dry-run 不启动 provider CLI,只验证命令形态、场景解析和报告路径:

```bash
cd /path/to/zaofu

PYTHONPATH="$(pwd)/src" python3 -m zf.cli.main autoresearch run \
  --scenario self-eval-backlog \
  --worktree /tmp/zaofu-autoresearch-dry
```

预期输出包含:

- `Autoresearch dry-run`
- `run_dir=...`
- `Report: .../report.md`

dry-run 适合检查 `--scenario`、`--worktree`、`--expected-done`、`--timeout`
等参数是否符合预期。

## 4. 推荐真实运行

推荐使用外层 tmux supervisor。外层 supervisor 负责观察和汇总,内层 harness 在独立 tmux session 中执行:

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

命令成功后会打印外层 session 名,形如:

```text
Autoresearch supervisor started in tmux session: zf-ar-supervisor-<run-id>
Attach with: tmux attach -t zf-ar-supervisor-<run-id>
```

进入外层 supervisor:

```bash
tmux attach -t zf-ar-supervisor-<run-id>
```

内层 harness session 形如:

```text
zf-autoresearch-<run-id>
```

如需直接看内层执行:

```bash
tmux attach -t zf-autoresearch-<run-id>
```

## 5. 运行中观察

外层 tmux supervisor 默认包含三个窗口:

| 窗口 | 作用 |
|---|---|
| `supervisor` | 执行 `zf autoresearch run --no-tmux ...`,等待内层 runner 结束并写报告 |
| `events` | 等待 `$WT/.zf/events.jsonl` 出现后持续 tail |
| `status` | 周期性执行 `zf status --workers` |

也可以从普通终端观察:

```bash
tail -f "$WT/.zf/events.jsonl"

cd "$WT"
PYTHONPATH=/path/to/zaofu/src python3 -m zf.cli.main status --workers

cd "$WT"
PYTHONPATH=/path/to/zaofu/src python3 -m zf.cli.main kanban --board
```

重点关注:

- `task.status_changed` 到 `done` 的数量是否达到 `--expected-done`。
- 是否出现 fatal event。
- 多 dev / multi-agent dispatch 是否真实分布到多个 worker。
- 是否卡在同一 task 或同一 role 上。
- cost 是否接近 `--budget-usd`。

当前 supervisor 视为 fatal 的事件类型包括:

- `orchestrator.dispatch_failed`
- `task.invalid_transition`
- `cost.budget.exceeded`
- `run.failed`
- `ship.failed`
- `task.orphaned`
- `worker.respawn.failed`
- `worker.recycle.failed`
- `worker.stuck.recovery_failed`

## 6. 输出产物

每次运行会生成一个 run 目录:

```text
$WT/.zf/autoresearch/runs/<run-id>/
```

核心文件:

| 文件 | 含义 |
|---|---|
| `scenario.json` | 本次场景、worktree、seed、timeout、budget 等 manifest |
| `inner-runner.log` | 内层 runner 标准输出和退出码 |
| `events-summary.json` | events 汇总、done 数、fatal event、dispatch 分布 |
| `iterations.tsv` | 用于趋势分析的单行迭代表 |
| `report.md` | 面向人类阅读的本次评估报告 |

优先读 `report.md`,再查 `events-summary.json` 和 `inner-runner.log`。

## 7. 失败写入 Backlog

加 `--backlog-on-failure` 后,当本次 run 未通过时,supervisor 会把失败写入
Kanban backlog。默认写入被测 worktree 的:

```text
$WT/.zf/kanban.json
```

如需写入指定 state dir:

```bash
PYTHONPATH="$(pwd)/src" python3 -m zf.cli.main autoresearch run \
  --scenario self-eval-backlog \
  --worktree "$WT" \
  --backlog-on-failure \
  --backlog-state-dir /path/to/.zf \
  --confirm
```

同一类失败会按稳定 key upsert,避免重复堆积同类任务。事件中 actor 为
`zf-autoresearch`,payload 会标记 `source = "autoresearch"`。

## 8. 常用参数

| 参数 | 默认值 | 说明 |
|---|---:|---|
| `--scenario` | `self-eval-backlog` | 内置评估场景名 |
| `--worktree` | 必填 | 被测隔离 worktree 路径 |
| `--config` | `examples/dev-codex-backends.yaml` | 生成被测 `zf.yaml` 的模板 |
| `--expected-done` | 场景默认值 | 期望完成 task 数 |
| `--timeout` | 场景默认值 | 内层 runner 超时时间,单位秒 |
| `--budget-usd` | `500` | 写入被测 `zf.yaml` 的全局预算 |
| `--reuse-worktree` | false | worktree 已存在时复用 |
| `--keep-running` | false | 内层 runner 结束后不自动 stop harness |
| `--runner-module` | `tests.e2e.run_mixed` | 内层 runner 模块 |
| `--run-id` | 自动生成 | 指定稳定 run id |
| `--output-dir` | `$WT/.zf/autoresearch/runs/<run-id>` | 指定 run 产物目录 |
| `--inject-worker-stuck` | false | 等目标 worker 收到真实 task 后注入 `autoresearch.inject.worker_stuck` |
| `--inject-worker-stuck-instance` | `dev-1` | stuck 注入目标 instance 或 role |
| `--inject-worker-stuck-timeout` | `600` | 目标 dispatch 长时间未出现时记录等待告警的秒数；不会提前关闭注入窗口 |
| `--tmux` | false | 启动外层 tmux supervisor |
| `--confirm` | false | 真正执行内层 provider run |

## 9. Deterministic Stuck 注入

`controlled-stuck-recovery` 建议显式打开 stuck 注入,否则只能依赖 pane
输出自然静默,真实 run 可能完成但没有覆盖 recovery 指标:

```bash
PYTHONPATH="$(pwd)/src" python3 -m zf.cli.main autoresearch run \
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

supervisor 会等待 `dev-1` 收到 `task.dispatched`,然后写入
`autoresearch.inject.worker_stuck`。内层 runtime 收到该事件后调用同一条
`worker.stuck -> task.requeued -> worker.stuck.recovered -> re-dispatch`
恢复路径。若前置 arch/critic/rework 阶段较慢,supervisor 只在日志中记录
`worker_stuck_injection=waiting`,不会因为等待超过
`--inject-worker-stuck-timeout` 就提前放弃注入；只有内层 runner 退出时目标
dispatch 仍未出现,本轮才会因 `stuck_injection_satisfied=false` 判失败。
签收时要求:

- `stuck_injection_requested_count >= 1`
- `worker_stuck_count >= 1`
- `worker_stuck_recovered_count >= 1`
- `worker_stuck_recovery_failed_count == 0`

## 10. 清理

结束后先停内层 harness:

```bash
cd "$WT"
PYTHONPATH=/path/to/zaofu/src python3 -m zf.cli.main stop 2>/dev/null || true
```

再清理 tmux:

```bash
tmux kill-session -t "zf-autoresearch-<run-id>" 2>/dev/null || true
tmux kill-session -t "zf-ar-supervisor-<run-id>" 2>/dev/null || true
```

确认产物已经归档或不需要后,删除 worktree:

```bash
cd /path/to/zaofu
git worktree remove "$WT" --force
```

## 10. 判定标准

一次 run 至少满足以下条件才算可接受:

- 内层 runner 退出码为 0。
- `task.status_changed -> done` 数量达到 `--expected-done`。
- 没有 fatal event。
- `report.md`、`events-summary.json`、`iterations.tsv` 都生成。
- 多 worker 场景中,dispatch 分布能证明任务不是单 worker 串行假跑。
- 若失败,失败被转化为可执行 backlog,并带有报告路径和复现命令。

当前 `self-eval-backlog` 场景的目标不是证明统计显著,而是用真实
long-horizon 链路持续暴露 harness / orchestration / verification / backlog
闭环中的实现缺陷。
