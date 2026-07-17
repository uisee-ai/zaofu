# Web, Observability, and E2E

> Audience: operators who need to inspect Kanban/runtime projections in a browser or run scripted and real-provider E2E validation.

## 1. Start the Web Dashboard

Install the Web dependencies:

```bash
uv sync --extra dev --extra web
```

For local access:

```bash
uv run zf web --host 127.0.0.1 --port 8001
```

For Docker Playwright or trusted LAN access:

```bash
uv run zf web --host 0.0.0.0 --port 5175
```

Only bind to `0.0.0.0` on a trusted network. To inspect a state directory from
another worktree or simulation:

```bash
uv run zf web \
  --state-dir /tmp/zaofu-run/.zf \
  --host 0.0.0.0 \
  --port 5175
```

### 1.1 Create a Project from the Workspace shell

For Project creation or registration, start the workspace shell with a
controlled action token:

```bash
export ZF_WEB_ACTION_TOKEN="$(openssl rand -hex 24)"
uv run zf web --host 127.0.0.1 --port 8001 --workspace-only
```

Bootstrap Inspect is read-only and proposes stack, Controller, setup, quality
checks, and instruction documents. Add Project / Initialize creates and
registers the Project but does not ignite a workflow. Clarification and explicit
approval are still required. See
[20 Project Creation, Bootstrap, and Workflow Ignition](20-project-bootstrap-workflow-ignition.en.md)
for screenshots and the complete path.

## 2. Observe a Running Harness

The Web application is a project-scoped dashboard. Its main pages are:

| Page | Purpose |
|---|---|
| Overview | Project summary, task flow, and key health signals |
| Inbox | Messages and alerts that need operator attention |
| Channels | Channel conversations, members, and streaming replies |
| Tasks | Kanban board and task inspector |
| Agents | Worker, role, provider, context, and token summaries |
| Automations | Daily Brief, Weekly Review, Project Monitor, and related jobs |
| Delivery | Feature/task delivery spine, stages, fanout, and ship readiness |
| Trace / Graph / Loop | Event causality, execution graph, and autoresearch/cycle traces |
| Observability | Events, logs, diagnostics, failed and blocked projections |
| Settings | Web/runtime settings and action-token state |

The Web UI is read-oriented by default. Task creation, channel membership,
maintenance preparation, runtime resume, and similar writes must use a
token-, passcode-, or trusted-session-gated controlled action. These actions
emit audit events and must not bypass kernel helpers to edit truth files.

Useful terminal views:

```bash
uv run zf kanban --board
uv run zf events --last 50
uv run zf watch --follow
uv run zf status --workers
uv run zf metrics snapshot
```

For one task:

```bash
uv run zf kanban show <task_id>
uv run zf task trace <task_id>
uv run zf runs for-task <task_id>
```

## 3. Docker Playwright

Run browser tests in Docker. Do not install browsers on the host unless that is
an explicit requirement:

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

Prerequisites:

- The ZaoFu Web/API process is listening on `0.0.0.0:5175`, or the configured port.
- Docker supports host networking.
- `$PWD` is the ZaoFu repository root.

## 4. Scripted E2E

Scripted E2E does not call a real provider. It validates the deterministic
kernel and pipeline:

```bash
uv run python -m tests.e2e.robustness_suite --smoke
uv run python -m tests.e2e.robustness_suite
```

The focused pytest subset is:

```bash
uv run pytest \
  tests/e2e/test_scripted_runner.py \
  tests/e2e/test_robustness_suite.py \
  tests/e2e/test_w5_phase_report.py \
  -q
```

## 5. Real Codex Smoke

A real Codex smoke starts provider processes, tmux, and actual workers, and it
consumes provider budget. Before running it, confirm that:

- `codex --version` succeeds.
- `codex login` has completed.
- `~/.codex/sessions` is writable.
- `examples/dev-codex-backends.yaml` validates.
- Explicit time and budget limits are set.

Recommended entry point:

```bash
uv run python -m tests.e2e.robustness_suite \
  --skip-unit \
  --skip-dry-run \
  --include-real codex \
  --confirm-real
```

Lower-level runner:

```bash
uv run python -m tests.e2e.run_mixed \
  --worktree /tmp/zaofu-codex-smoke \
  --config examples/dev-codex-backends.yaml \
  --seed-file tests/e2e/seeds/large_dev_split_3_tasks.txt \
  --expected-done 1 \
  --timeout 1800 \
  --confirm
```

After the run:

```bash
uv run python -m tests.e2e.mixed_phase_report \
  --state-dir /tmp/zaofu-codex-smoke/.zf

uv run python -m tests.e2e.verify_real_state_web \
  --state-dir /tmp/zaofu-codex-smoke/.zf \
  --base-url http://127.0.0.1:5175
```

## 6. Full-Stack Validation Scorecard

The scorecard condenses evidence from an existing real E2E run into an
auditable report. It does not start workers. It checks issue, PRD, and refactor
intake; key Web projections; New Task, Kanban Agent, and Channel entry points;
fanout workflow evidence; and real Codex hook and usage evidence.

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

Or use the wrapper:

```bash
tests/e2e/run_real_state_web_validation.sh \
  /tmp/zaofu-codex-smoke/.zf \
  /tmp/zf-full
```

Review `matrix`, `fanout_trace_chain`, `codex_hook_usage`, and
`summary.failed`. `--require-real-codex` fails when real CLI, session, or usage
evidence is missing, preventing a mock or partial run from being reported as a
real-provider pass.

## 7. Run Archive

Archive live state:

```bash
uv run zf archive-run \
  --run-id "run-$(date -u +%Y%m%d%H%M%S)" \
  --live-state-dir /tmp/zaofu-codex-smoke/.zf \
  --status passed
```

Inspect or rebuild archive projections:

```bash
uv run zf runs list
uv run zf runs rebuild
```

## 8. L0-L5 Evaluation Levels

| Level | Goal | Typical entry point |
|---|---|---|
| L0 | Static config, schema, skill, and topology checks | `zf validate`, `zf skills doctor` |
| L1 | Deterministic unit and integration tests | `pytest tests/...` |
| L2 | Complete scripted flow | `tests.e2e.scripted_runner`, `robustness_suite --smoke` |
| L3 | Real smoke with one provider | `robustness_suite --include-real codex --confirm-real` |
| L4 | Multi-worker stress and recovery | `tests.e2e.run_mixed`, autoresearch scenarios |
| L5 | Web/API projections and operator inspection | `zf web`, Docker Playwright, `verify_real_state_web` |

Do not skip L0-L2 and immediately spend real-provider budget. When a real run
fails, archive its evidence before creating repair backlogs or tasks.
