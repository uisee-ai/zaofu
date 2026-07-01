# Web、观测与 E2E

> 适用对象: 需要在浏览器看 Kanban/runtime projection,或运行真实/脚本化 E2E 的操作者。

## 1. 启动 Web Dashboard

安装依赖:

```bash
uv sync --extra dev --extra web
```

本地访问:

```bash
uv run zf web \
  --host 127.0.0.1 \
  --port 8001
```

给 Docker Playwright 或局域网测试访问:

```bash
uv run zf web \
  --host 0.0.0.0 \
  --port 5175
```

只在可信网络使用 `0.0.0.0`。如果调试另一个 worktree 的 state:

```bash
uv run zf web \
  --state-dir /tmp/zaofu-run/.zf \
  --host 0.0.0.0 \
  --port 5175
```

## 2. 运行中观测

终端观测:

```bash
uv run zf kanban --board
uv run zf events --last 50
uv run zf watch --follow
uv run zf status --workers
uv run zf metrics snapshot
```

针对单个 task:

```bash
uv run zf kanban show <task_id>
uv run zf task trace <task_id>
uv run zf runs for-task <task_id>
```

## 3. Docker Playwright

Web Playwright 测试默认用 Docker,不要在宿主机安装浏览器:

```bash
docker volume create zaofu-pw-browsers >/dev/null
docker run --rm --user root --entrypoint /bin/sh --network host \
  -v "$PWD:/workspace" \
  -v zaofu-pw-browsers:/tmp/ms-playwright \
  -w /workspace/web \
  -e PLAYWRIGHT_BROWSERS_PATH=/tmp/ms-playwright \
  -e ZF_WEB_BASE_URL=http://127.0.0.1:5175 \
  mcp/playwright:latest \
  -lc "npx playwright install chromium && npx playwright test --project=chromium --workers=1"
```

前置条件:

- ZaoFu Web/API 已在 `0.0.0.0:5175` 或对应端口启动。
- Docker 支持 `--network host`。
- 当前 `$PWD` 是 ZaoFu repo 根目录。

## 4. Scripted E2E

脚本化 E2E 不调用真实 provider,用于验证 deterministic kernel 和 pipeline:

```bash
uv run python -m tests.e2e.robustness_suite --smoke
uv run python -m tests.e2e.robustness_suite
```

也可以直接运行 pytest 子集:

```bash
uv run pytest \
  tests/e2e/test_scripted_runner.py \
  tests/e2e/test_robustness_suite.py \
  tests/e2e/test_w5_phase_report.py \
  -q
```

## 5. 真实 Codex Smoke

真实 Codex smoke 会启动 provider、tmux 和实际 worker,会消耗预算。先确认:

- `codex --version` 可用。
- `codex login` 已完成。
- `~/.codex/sessions` 可写。
- `examples/dev-codex-backends.yaml` validate 通过。
- 已设置预算和超时。

推荐入口:

```bash
uv run python -m tests.e2e.robustness_suite \
  --skip-unit \
  --skip-dry-run \
  --include-real codex \
  --confirm-real
```

更底层的 runner:

```bash
uv run python -m tests.e2e.run_mixed \
  --worktree /tmp/zaofu-codex-smoke \
  --config examples/dev-codex-backends.yaml \
  --seed-file tests/e2e/seeds/large_dev_split_3_tasks.txt \
  --expected-done 1 \
  --timeout 1800 \
  --confirm
```

真实 run 完成后:

```bash
uv run python -m tests.e2e.mixed_phase_report \
  --state-dir /tmp/zaofu-codex-smoke/.zf

uv run python -m tests.e2e.verify_real_state_web \
  --state-dir /tmp/zaofu-codex-smoke/.zf \
  --base-url http://127.0.0.1:5175
```

## 6. Full-stack Validation Scorecard

Full-stack validation scorecard 用于把真实 E2E 的证据收敛成可审计报告。它不会启动新的 worker,只读取已有 state,检查 issue / PRD / refactor 三类任务入口、Web dashboard 关键投影、new task / Kanban Agent / channel 三条入口、channel 与 Kanban Agent 触发 fanout workflow 的证据,以及真实 Codex hook / usage 证据。

推荐在真实 run 后执行:

```bash
PYTHONPATH=src python -m tests.e2e.full_stack_validation \
  --state-dir /tmp/zaofu-codex-smoke/.zf \
  --repo-root "$PWD" \
  --require-real-codex \
  --require-docker \
  --preflight-output /tmp/zf-full/preflight.json \
  --output /tmp/zf-full/scorecard.json \
  --markdown /tmp/zf-full/report.md
```

也可以使用包装脚本:

```bash
tests/e2e/run_real_state_web_validation.sh \
  /tmp/zaofu-codex-smoke/.zf \
  /tmp/zf-full
```

报告重点看 `matrix`, `fanout_trace_chain`, `codex_hook_usage`, `summary.failed`。`--require-real-codex` 会在缺少真实 Codex CLI / session / usage 证据时失败,避免把 mock 或半成品 E2E 误判为通过。

## 7. Run Archive

归档 live state:

```bash
uv run zf archive-run \
  --run-id "run-$(date -u +%Y%m%d%H%M%S)" \
  --live-state-dir /tmp/zaofu-codex-smoke/.zf \
  --status passed
```

查看归档:

```bash
uv run zf runs list
uv run zf runs rebuild
```

## 8. L0-L5 评估层级

鲁棒性评估可按以下层级推进:

| 层级 | 目标 | 常用入口 |
|---|---|---|
| L0 | 静态配置、schema、skill、拓扑检查 | `zf validate`, `zf skills doctor` |
| L1 | deterministic unit/integration | `pytest tests/...` |
| L2 | 脚本化完整流程 | `tests.e2e.scripted_runner`, `robustness_suite --smoke` |
| L3 | 单 provider 真实 smoke | `robustness_suite --include-real codex --confirm-real` |
| L4 | 多 worker 压力和恢复 | `tests.e2e.run_mixed`, autoresearch scenarios |
| L5 | Web/API projection 与人工观测 | `zf web`, Docker Playwright, `verify_real_state_web` |

不要跳过 L0-L2 直接烧真实 provider。真实 run 失败后,先归档 evidence,再生成 backlog 和修复任务。
