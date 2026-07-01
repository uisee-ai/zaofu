# ZaoFu

> AI Agent Delivery Control Plane for long-horizon software work.

[中文说明](README.zh-CN.md)

ZaoFu turns AI coding agents from isolated chat sessions into a governed
delivery team. It does not replace Claude Code, Codex, OpenClaw, or other
coding agents. It gives them roles, task contracts, runtime context, evidence
requirements, recovery paths, and an event-sourced control plane.

```text
普通 coding agent:
  prompt -> agent writes code -> agent says done

ZaoFu:
  idea / issue / refactor
    -> plan / task-map
    -> role workers
    -> evidence / review / verify
    -> deterministic gates
    -> done / rework / escalation
```

The product promise is not "the model is smarter." The promise is that
agentic software delivery becomes **configurable, observable, recoverable,
auditable, and evidence-gated**.

---

## Why ZaoFu Exists

AI coding agents are already strong at local code generation. The harder
problem is engineering control:

- long-running tasks drift from the original goal;
- parallel agents overwrite, duplicate, or block each other;
- "done" is often a chat claim instead of a verified state transition;
- review, test, and runtime evidence is scattered across terminal output;
- operators need progress, blockers, cost, risk, and delivery proof without
  reading every transcript.

ZaoFu treats coding agents as useful but untrusted workers inside a
deterministic harness. Agents can plan, implement, review, test, and report
intent. The kernel owns runtime truth and state transitions.

## Best Fit

ZaoFu is designed for teams already using coding agents and now needing
delivery discipline around them:

- large refactors and migration projects;
- multi-module product delivery;
- issue / bug fixing with regression evidence;
- test coverage and quality hardening;
- long-horizon AI-native engineering workflows;
- operator-visible Kanban, traces, Feishu, and channel collaboration.

ZaoFu is not a general workflow automation platform, and it is not a
single-agent coding assistant.

## Core Capabilities

- **`zf.yaml` control plane**: roles, stages, triggers, providers, gates,
  budgets, recovery policy, and workflow topology.
- **Multi-agent execution**: orchestrator, architect, critic, dev, review,
  test, judge, Kanban Agent, Channel members, and provider-backed workers.
- **Event-sourced runtime truth**: `events.jsonl`, `kanban.json`,
  `session.yaml`, `feature_list.json`, and `role_sessions.yaml`.
- **Evidence-gated completion**: TaskContract, static gates, review/test/judge
  signals, discriminator checks, and artifact refs.
- **Long-horizon recovery**: heartbeat, stuck detection, context recovery,
  bounded rework, run manager, and delivery trace.
- **Operator cockpit**: Web dashboard, Kanban, Agent sessions, Delivery Trace,
  Inbox, Channel, Feishu, and projection-based observability.
- **Self-improvement loops**: Supervisor, Autoresearch, run recovery,
  self-repair proposals, and backlog synthesis.

## Architecture

```text
                 zf.yaml
        single control-plane config
                    |
                    v
┌────────────────────────────────────────────────────────────┐
│ Layer 1: deterministic kernel                              │
│ EventLog / EventWriter / TaskStore / gates / projections   │
│ token-gated actions / recovery / state reconciliation      │
└───────────────────────┬────────────────────────────────────┘
                        |
                        v
┌────────────────────────────────────────────────────────────┐
│ Layer 2: orchestrator brain                                │
│ plans, splits, routes, replans, escalates                  │
│ writes truth only through zf CLI or controlled actions     │
└───────────────────────┬────────────────────────────────────┘
                        |
                        v
┌────────────────────────────────────────────────────────────┐
│ Layer 3: hands / workers                                   │
│ arch / critic / dev / review / test / judge / providers    │
│ work from briefings and emit structured evidence events    │
└────────────────────────────────────────────────────────────┘
```

Key invariant:

> Agents may propose and execute work. ZaoFu decides state through the
> deterministic kernel and append-only events.

## Main Business Loops

ZaoFu is a set of business and runtime loops, not one giant loop:

| Loop | Shape |
|---|---|
| Delivery | `idea -> plan -> task-map -> impl -> verify -> ship` |
| Quality | `evidence -> gate -> pass or bounded rework` |
| Human approval | `plan hold -> Web/Feishu approve -> fanout unlock` |
| Channel collaboration | `discussion -> synthesis -> workflow intent` |
| Kanban Agent | `operator request -> proposal/action -> projection` |
| Run recovery | `observe -> decide -> controlled action -> post-verify` |
| Autoresearch | `failure -> diagnosis/proposal -> gate -> repair path` |
| Replan | `drift/insight -> proposal -> contract eval -> adoption` |
| Module parity | `verify -> parity scan -> gap plan -> task-map amend` |
| Observability | `events -> projections -> Web/CLI -> controlled action` |

## Repository Layout

```text
src/zf/
  cli/                 zf CLI entrypoints
  core/                config, events, task stores, workflow graph
  runtime/             orchestrator/runtime loops, channels, run manager
  integrations/        Feishu and external integration edges
  autoresearch/        outer evaluation and self-improvement loops
  web/                 FastAPI app and read projections

web/                   React dashboard
examples/              workflow and provider configuration examples
docs/manual/           user-facing manuals
tests/                 deterministic and E2E tests
tools/                 local operation scripts
```

Runtime state belongs in the configured `project.state_dir` (default `.zf/`).
It is not source code.

## Requirements

- Python 3.11+
- `uv`
- `tmux`
- At least one coding-agent provider CLI for real worker runs:
  - `codex`
  - `claude`
  - another configured backend

Optional:

- Node.js / npm for the Web dashboard frontend build.
- Docker for Playwright browser E2E.
- Feishu credentials for ChatOps and approval cards.

## Install From Source

```bash
git clone <repo-url> zaofu
cd zaofu

uv sync --extra dev --extra web --extra stream-json
uv run zf --version
uv run pytest
```

For a smaller CLI-only environment:

```bash
uv sync --extra dev
uv run zf --help
```

## Quick Start

Create or inspect `zf.yaml`, initialize runtime state, validate, dry-run, then
start:

```bash
uv run zf presets
uv run zf init --preset safe-team --workspace-register

uv run zf validate --cold-start
uv run zf start --dry-run --no-watch

uv run zf start
```

In another terminal:

```bash
uv run zf chat "Implement a small feature with tests and review evidence."
uv run zf kanban --board
uv run zf events --last 30
```

Useful preflight checks:

```bash
command -v tmux
command -v codex      # if using Codex
command -v claude     # if using Claude Code

uv run zf doctor provider --backend codex
uv run zf skills doctor
uv run zf workflow inspect
```

For a brand-new or external project, prefer the bootstrap script:

```bash
tools/init-project.sh \
  --project-dir /path/to/my-project \
  --preset safe-team \
  --yes
```

With an existing config:

```bash
tools/init-project.sh \
  --project-dir /path/to/my-project \
  --source-config /path/to/my-project/zf-codex.yaml \
  --yes
```

More detail: [docs/manual/01-quickstart.md](docs/manual/01-quickstart.md).

## Web Dashboard

For local development, use the helper script. It builds `web/dist`, starts the
FastAPI dashboard in tmux, loads `.env`, and writes/reuses the Web action token:

```bash
tools/start-webkanban.sh
tools/start-webkanban.sh --status
tools/start-webkanban.sh --stop
```

Default URL:

```text
http://127.0.0.1:8001/
```

For LAN / Docker Playwright access:

```bash
tools/start-webkanban.sh --host 0.0.0.0 --port 8001
```

Only bind `0.0.0.0` on a trusted network. Web mutations are token-gated through
`ZF_WEB_ACTION_TOKEN` or the generated token file under `~/.zaofu`.

More detail:

- [docs/manual/06-web-observability-e2e.md](docs/manual/06-web-observability-e2e.md)
- [docs/manual/09-zaofu-cli-usage.md](docs/manual/09-zaofu-cli-usage.md)

## Common Commands

```bash
# Config and startup
uv run zf validate --path zf.yaml
uv run zf validate --cold-start
uv run zf start --dry-run --no-watch
uv run zf start
uv run zf stop

# Runtime observation
uv run zf status --workers
uv run zf kanban --board
uv run zf events --last 50
uv run zf watch --follow
uv run zf trace show <trace_id>

# Tasks and evidence
uv run zf kanban add "Fix login expiry bug"
uv run zf task trace <task_id>
uv run zf runs for-task <task_id>
uv run zf gate list

# Workspace and Web
tools/start-webkanban.sh --status
uv run zf web --host 127.0.0.1 --port 8001
```

Full CLI reference:
[docs/manual/09-zaofu-cli-usage.md](docs/manual/09-zaofu-cli-usage.md).

## Workflow Examples

Representative examples:

| Example | Use case |
|---|---|
| `examples/safe-team.yaml` | standard multi-role local team |
| `examples/design-first.yaml` | design-first delivery flow |
| `examples/dev-codex-backends.yaml` | all-Codex development smoke topology |
| `examples/dev-mixed-backends.yaml` | mixed-backend stress topology |
| `examples/zf-full-codex.yaml` | full Codex delivery DAG |
| `examples/prod/prd-fanout-codex.yaml` | PRD fanout product delivery |
| `examples/prod/issue-fanout-codex.yaml` | issue/bug fanout delivery |
| `examples/prod/refactor-flow-codex.yaml` | refactor delivery flow |

Validate any example before use:

```bash
uv run zf validate --path examples/safe-team.yaml
```

## Feishu / ChatOps

ZaoFu supports a direct Feishu bridge:

```bash
uv sync --extra feishu
uv run zf feishu bridge --watch
```

The bridge can route Feishu messages into project channels, Kanban Agent,
Run Manager Agent, or provider-backed coding-agent conversations. It also
supports plan approval cards that unlock gated fanout execution through the
same controlled-action path used by Web.

More detail:

- [docs/manual/19-feishu-ai-native-direct-bridge.md](docs/manual/19-feishu-ai-native-direct-bridge.md)
- [docs/manual/11-feishu-automation-kanban-sync.md](docs/manual/11-feishu-automation-kanban-sync.md)
- [docs/manual/15-channel-collaboration.md](docs/manual/15-channel-collaboration.md)

## Autoresearch and Robustness

Autoresearch is the outer evaluation and self-improvement loop. It runs
scenarios against ZaoFu, records evidence, detects failure patterns, and can
produce repair proposals or backlog candidates.

Start with a dry run:

```bash
uv run zf autoresearch run \
  --scenario controlled-stuck-recovery \
  --worktree /tmp/zf-autoresearch-dry \
  --config examples/dev-codex-backends.yaml
```

Real provider runs are opt-in and can consume model budget:

```bash
uv run zf autoresearch run \
  --scenario controlled-stuck-recovery \
  --worktree /tmp/zf-autoresearch-real \
  --config examples/dev-codex-backends.yaml \
  --expected-done 1 \
  --timeout 7200 \
  --budget-usd 180 \
  --tmux \
  --confirm
```

More detail:

- [docs/manual/10-autoresearch-usage.md](docs/manual/10-autoresearch-usage.md)
- [docs/manual/autoresearch-orchestrator.md](docs/manual/autoresearch-orchestrator.md)
- [docs/manual/16-real-codex-provider-preflight.md](docs/manual/16-real-codex-provider-preflight.md)
- [docs/manual/18-product-fanout-real-e2e.md](docs/manual/18-product-fanout-real-e2e.md)

## Testing

Fast local checks:

```bash
uv run pytest
npm --prefix web run build
```

Focused Web / Channel / Kanban Agent audit:

```bash
tests/e2e/scripts/run_web_interactive_e2e_audit.sh --skip-docker
```

Deterministic robustness suite:

```bash
tests/e2e/scripts/run_robustness_suite.sh --smoke
tests/e2e/scripts/run_robustness_suite.sh
```

Real provider smoke:

```bash
tests/e2e/scripts/run_robustness_suite.sh \
  --include-real codex \
  --confirm-real
```

More detail: [docs/manual/06-web-observability-e2e.md](docs/manual/06-web-observability-e2e.md).

## Documentation Map

Start here:

- [docs/manual/00-index.md](docs/manual/00-index.md) — user manual index
- [docs/manual/architecture.md](docs/manual/architecture.md) — architecture overview
- [docs/manual/01-quickstart.md](docs/manual/01-quickstart.md) — first run
- [docs/manual/02-zf-yaml-control-plane.md](docs/manual/02-zf-yaml-control-plane.md) — `zf.yaml`
- [docs/manual/03-cli-operations.md](docs/manual/03-cli-operations.md) — daily operations
- [docs/manual/04-harness-runtime.md](docs/manual/04-harness-runtime.md) — runtime flow
- [docs/manual/13-plan-task-map-orchestrator-dispatch.md](docs/manual/13-plan-task-map-orchestrator-dispatch.md) — plan to task-map to dispatch
- [docs/manual/14-delivery-trace-usage.md](docs/manual/14-delivery-trace-usage.md) — delivery trace


## Safety and Boundaries

- `zf.yaml` is the only control-plane config.
- Runtime state belongs to `project.state_dir`; do not commit `.zf/`.
- `events.jsonl` is append-only runtime truth.
- Web/API/integrations should mutate state only through token-gated
  controlled actions or deterministic kernel paths.
- Provider CLIs can spend money and modify files. Run dry-runs and preflight
  checks before real provider execution.
- Do not expose Web dashboard or Feishu bridges on untrusted networks.

## Current Status

ZaoFu is implementation-active. The deterministic kernel, CLI, runtime,
Web dashboard, workflow examples, Feishu bridge, Channel, Run Manager, and
Autoresearch paths exist in this repository. APIs and workflow presets are
still evolving, so validate `zf.yaml` and run dry-runs before relying on a new
configuration.

```bash
uv run zf validate --cold-start
uv run zf start --dry-run --no-watch
```
