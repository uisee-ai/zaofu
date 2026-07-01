# Autoresearch Campaign 使用手册

> 适用对象: 想把多个 autoresearch 指标场景作为一组 long-horizon 验收批次执行的操作者。

## 1. 作用

`autoresearch campaign` 不直接启动 provider。它生成一组可执行的场景计划:

- `campaign.json`: 机器可读的场景、指标、断言、命令。
- `campaign.md`: 人可读的验收说明。
- `run-campaign.sh`: 顺序执行各场景的脚本。

真实执行仍由 `zf autoresearch run` 完成,每个场景使用独立 worktree/state_dir。

## 2. 生成计划

```bash
cd /path/to/zaofu

PYTHONPATH="$(pwd)/src" python3 -m zf.cli.main autoresearch campaign plan \
  --campaign full-validation \
  --output-dir /tmp/zaofu-ar-campaign-plan \
  --worktree-root /tmp/zaofu-ar-campaign \
  --config examples/dev-codex-backends.yaml
```

不希望脚本使用 tmux supervisor 时加:

```bash
--no-tmux
```

## 3. 当前内置 Campaign

`full-validation` 是全功能 deterministic planning campaign,覆盖当前六个
built-in scenarios。它只生成计划,不直接启动真实 provider 长跑:

| 场景 | 目标 |
|---|---|
| `controlled-stuck-recovery` | 先跑的单场景 smoke,验证 stuck/requeue/recovery |
| `positive-pressure-4dev` | 四个独立任务压测 dev/test replica、handoff 和 terminal evidence |
| `fail-rework-converge` | 验证 fail-closed 后 bounded rework 能收敛 |
| `manual-intervention-guard` | 验证人工/外部状态介入不能绕过 truth kernel |
| `self-eval-backlog` | 验证 self-eval failure backlog、pass no-op、repair contract 和 docs evidence |
| `spec-validate-hardening` | 验证 verification literal 与 tdd_ref scope graph hardening |

`harness-hardening` 是较小的四场景主干 campaign,不包含
`self-eval-backlog` 和 `spec-validate-hardening`。

## 4. Full Validation 执行顺序

不要一开始直接执行 `run-campaign.sh`。推荐顺序固定为:

### Phase 0: 无 provider 预检

只做本地 deterministic 检查和 plan 生成:

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

### Phase 1: 单场景 smoke

先只跑 `controlled-stuck-recovery`,并显式设置预算和超时。该阶段用于证明
stuck 注入与恢复路径可用,失败时不要继续扩大验证面。

### Phase 2: 全量按序扩展

Phase 1 通过后,再按生成的 `campaign.json` 顺序逐个运行:

1. `controlled-stuck-recovery`
2. `positive-pressure-4dev`
3. `fail-rework-converge`
4. `manual-intervention-guard`
5. `self-eval-backlog`
6. `spec-validate-hardening`

每个场景使用独立 worktree/state_dir。不要复用失败 run 的 worktree 继续扩展。

## 5. 预算、失败和清理规则

- `campaign.json` 为每个 scenario 写出 `budget_usd` 和 `timeout_seconds`;真实
  provider run 必须带对应 `--budget-usd`、`--timeout` 和 `--confirm`。
- 预算不足或超时不是通过;记录 report 后停止后续场景。
- 每个真实 run 都带 `--backlog-on-failure`。若场景失败,先用该场景的
  `report.md`、`events-summary.json` 和 failure backlog 修复,然后单独重跑失败
  scenario。
- 清理前先保留 run 证据: `<worktree>/.zf/autoresearch/runs/<run-id>/report.md`、
  `events-summary.json`、`inner-runner.log`。
- run 结束后执行对应 worktree 的 `zf stop`;若使用 tmux,关闭
  `zf-ar-<campaign>-<scenario>` session;确认无用后再删除 `/tmp/zf-ar-*`
  临时 worktree。

## 6. 关注指标

每个 `autoresearch run` 的 `events-summary.json` 现在包含 `derived_metrics`:

| 指标 | 含义 |
|---|---|
| `fatal_count` | fatal event 数量 |
| `stuck_injection_requested_count` | autoresearch 是否显式请求 stuck 注入 |
| `stuck_injection_satisfied` | 注入后是否观察到 stuck 和 recovered 且无 recovery_failed |
| `worker_stuck_count` | stuck 事件数量 |
| `worker_stuck_recovered_count` | stuck 后恢复数量 |
| `worker_stuck_recovery_failed_count` | stuck 恢复失败数量 |
| `task_done_blocked_count` | terminal evidence 被阻断次数 |
| `done_evidence_count` | `task.done.evidence` 数量 |
| `terminal_evidence_coverage` | done task 中有 done evidence 的比例 |
| `discriminator_passed_count` / `discriminator_failed_count` | discriminator 结果数量 |
| `invalid_transition_count` | 非法状态迁移数量 |
| `duplicate_success_event_count` | 同一 task/event/dispatch 的重复成功事件数量 |
| `rework_signal_count` | review/test/judge/gate/discriminator/block 触发的 rework 信号数 |
| `dev_replicas_used` / `test_replicas_used` | 实际使用的 dev/test replica |

## 7. 签收口径

一次 campaign 不以“脚本跑完”作为唯一结论。最小签收条件:

- 每个场景都有 `report.md` 和 `events-summary.json`。
- 每个场景满足 `tasks_done >= expected_done`。
- 所有场景 `fatal_count == 0`。
- `duplicate_success_event_count == 0`。
- 所有 terminal done 都有 `task.done.evidence`。
- stuck 场景必须显式记录 `stuck_injection_requested_count >= 1`、
  `worker_stuck_count >= 1`、`worker_stuck_recovered_count >= 1`。
- stuck 场景没有 `worker.stuck.recovery_failed`。

如果某个场景失败,不要继续扩大测试面。先根据该场景 report 生成修复 backlog,修复后单独重跑该场景。
