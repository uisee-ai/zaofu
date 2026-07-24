# ZaoFu Operator Manual

> Audience: operators, engineering leads, and contributors who need to run,
> observe, and troubleshoot a ZaoFu multi-agent delivery workflow.
>
> Last verified against the CLI: 2026-07-17.
>
> This is the consolidated English manual. The topic-specific Chinese manuals
> in this directory remain the deeper reference for individual subsystems.

## 1. What ZaoFu Is

ZaoFu is a multi-agent harness for long-horizon software delivery. It does not
replace Codex, Claude Code, or another coding-agent CLI. It places those agents
inside a governed delivery system with:

- explicit roles and task contracts;
- deterministic state transitions and quality gates;
- isolated workdirs and Git evidence;
- bounded rework and recovery;
- operator-visible Kanban, traces, channels, and integrations;
- append-only runtime evidence.

The core promise is not that a model becomes smarter. The promise is that
agentic delivery becomes configurable, observable, recoverable, auditable, and
evidence-gated.

```text
idea / PRD / issue / refactor
  -> workflow request
  -> scan / plan / task map
  -> implementation workers
  -> review / verify / judge
  -> deterministic gates
  -> done, rework, or escalation
```

ZaoFu is best suited to large refactors, multi-module product work, regression
fixes, quality hardening, and other work where a single chat session is not a
reliable delivery process.

## 2. Operating Model

### 2.1 Three layers

| Layer | Owner | Responsibility |
|---|---|---|
| Layer 1 | deterministic kernel | Events, state transitions, schemas, gates, dispatch safety, replay, and external effects |
| Layer 2 | orchestrator and controllers | Goal decomposition, routing, replan and rework decisions |
| Layer 3 | worker agents | Architecture, implementation, review, testing, and evidence production |

Agents report intent and evidence. They do not directly own runtime truth.

### 2.2 Single control plane

`zf.yaml` is the canonical control-plane configuration. It declares roles,
providers, stages, triggers, budgets, workdir isolation, skills, gates, and
recovery policy.

The runtime state directory comes from `project.state_dir` and defaults to
`.zf/`. Do not hard-code `.zf` in integrations or automation.

### 2.3 Runtime truth

The kernel manages these primary state files:

| Path | Meaning |
|---|---|
| `events.jsonl` | Append-only event log |
| `kanban.json` | Active task state; terminal tasks are archived under `kanban/` |
| `feature_list.json` | Active feature state; terminal features are archived |
| `session.yaml` | Harness session state |
| `role_sessions.yaml` | Role instance to provider-session bindings |

Artifacts, projections, skills materialization, Web read models, transcripts,
and diagnostics are supporting state. They must not become a second control
plane.

Never hand-edit runtime truth. Use `zf` commands, `EventWriter`, `TaskStore`,
`FeatureStore`, or another sanctioned kernel action.

### 2.4 Typical delivery lifecycle

```text
user.message
  -> plan and task-map artifacts
  -> task.created / task.assigned
  -> dev.build.done
  -> review.approved or review.rejected
  -> test.passed or test.failed
  -> judge.passed or judge.failed
  -> discriminator.passed
  -> task done
```

The exact topology is defined by the active `zf.yaml`. Product flows may use
fanout/fanin, dedicated plan stages, candidate integration, verifier workers,
or different terminal predicates.

## 3. Requirements and Installation

Minimum requirements:

- Python 3.11 or newer;
- `uv`;
- Git;
- `tmux` when the configured transport uses tmux;
- at least one configured provider CLI for a real run, such as `codex` or
  `claude`.

Install from a source checkout:

```bash
git clone <repo-url> zaofu
cd zaofu

uv sync --extra dev --extra web --extra stream-json --extra feishu
uv run zf --version
```

The optional extras are intentionally separate:

| Extra | Use |
|---|---|
| `dev` | Tests and contributor tooling |
| `web` | FastAPI/Uvicorn dashboard |
| `stream-json` | Claude Code stream-json transport |
| `feishu` | Official Feishu/Lark WebSocket SDK |

Check the provider environment before a real run:

```bash
command -v git
command -v tmux
command -v codex      # when using Codex
command -v claude     # when using Claude Code

uv run zf doctor provider --backend codex
claude --version
```

Provider login and host sandbox support are external prerequisites. A valid
`zf.yaml` cannot repair a missing or unauthenticated provider CLI.

## 4. Initialize a Project

### 4.1 Preferred bootstrap path

For a new or external project, use the bootstrap script:

```bash
tools/init-project.sh \
  --project-dir /path/to/project \
  --preset safe-team \
  --yes
```

With an existing configuration:

```bash
tools/init-project.sh \
  --project-dir /path/to/project \
  --source-config /path/to/project/zf-codex.yaml \
  --yes
```

The script prepares `zf.yaml`, initializes runtime state, updates project
instructions, registers the workspace when requested, checks Git requirements,
and performs a dry-run preflight.

### 4.2 Direct CLI path

```bash
cd /path/to/project

uv run zf presets
uv run zf init --preset safe-team --workspace-register --with-bootstrap
uv run zf validate --cold-start
```

Useful alternatives:

```bash
uv run zf init --skip-instruction-docs
uv run zf init --no-workspace-register
uv run zf init --env-check
```

`zf init --force` reinitializes runtime truth. Do not use it on a state
directory whose evidence must be retained.

### 4.3 Project workflow container

Create a long-lived, multi-kind Project container by omitting `--kind`:

```bash
uv run zf project init \
  --name my-product \
  --root /path/to/my-product \
  --create \
  --git-init \
  --backend claude-code \
  --workspace-register
```

Initialization creates Project config and runtime state but does not ignite a
workflow. Explicit `--kind issue|prd|refactor` remains a compatibility path for
a single-kind Controller. `--request-kind` describes an optional initial
Request; only an explicit `--apply` may submit it, and readiness still fails
closed on missing fields or open questions.

The `zf flow` commands create intake, classification, clarification, draft, and
preflight artifacts. Use `flow clarify --confirm` to confirm the requirement
snapshot, `flow submit --dry-run` to preview admission, and `flow submit
--apply` for explicit ignition. See
[Project Creation, Bootstrap, and Workflow Ignition](20-project-bootstrap-workflow-ignition.en.md)
for the complete CLI and Web path.

## 5. Configure `zf.yaml`

A minimal shape is:

```yaml
version: "1.0"
project:
  name: my-project
  state_dir: .zf
session:
  tmux_session: zf-my-project
orchestrator:
  backend: claude-code
roles:
  - name: dev
    backend: claude-code
    role_kind: writer
    triggers: [task.assigned]
    publishes: [dev.build.done]
```

Important configuration groups:

| Group | Purpose |
|---|---|
| `project` | Project identity and runtime state directory |
| `session` | tmux session and layout |
| `orchestrator` | Layer 2 backend and turn policy |
| `roles` | Worker roles, replicas, providers, triggers, publications, and limits |
| `skill_sources` | Read-only skill roots |
| `runtime` | Workdirs, Git isolation, skill materialization, Run Manager |
| `workflow` | Stage graph, fanout/fanin, rework routes, recovery policy |
| `quality_gates` | Deterministic command gates |
| `verification` | Contract, scope, architecture, and promoted-rule checks |
| `integrations` | Feishu and external adapters |
| `autoresearch` | Trigger, resident, review, and repair policy |
| `global_budget_usd` | Global dispatch budget |

Validate configuration against the current implementation:

```bash
uv run zf validate --path zf.yaml
uv run zf validate --cold-start
uv run zf validate --strict-skills
uv run zf workflow inspect
uv run zf preflight --path zf.yaml
```

Design documents may describe planned fields. The current CLI, config schema,
loader, and runtime callers define what is implemented.

## 6. Roles, Skills, and Workdirs

### 6.1 Roles

Common roles include `orchestrator`, `arch`, `critic`, `dev`, `review`,
`test`, and `judge`. A role may declare replicas and provider backends.

`name` is a logical role. An expanded `instance_id`, such as `dev-1`, is the
runtime worker identity.

Use `role_kind` to express source access:

| Kind | Expected behavior |
|---|---|
| `writer` | Works on an isolated branch/worktree and may create source changes |
| `reader` | Reviews or verifies a pinned candidate and should not modify delivery truth |
| `auto` | Runtime infers the behavior for compatibility |

### 6.2 Skills

Roles enable skills by name. ZaoFu resolves configured sources, detects
collisions, materializes role-specific skills, and records the result in a
lock/projection.

```bash
uv run zf skills list
uv run zf skills doctor
uv run zf validate --strict-skills
```

Project-specific semantics belong in skills, prompts, and agent artifacts when
possible. Deterministic code should remain focused on schemas, invariants,
state transitions, evidence checks, security, replay, and external effects.

### 6.3 Workdir and Git evidence

Enable runtime worktree isolation with:

```yaml
runtime:
  workdirs:
    enabled: true
    root: .zf/workdirs
    mode: worktree
  git:
    writer_branch_prefix: worker
    task_ref_prefix: task
    candidate_branch_prefix: candidate
    candidate_base_ref: main
    candidate_strategy: cherry-pick
```

Git evidence should identify the base, source commit, changed paths, candidate
ref, verification commands, and dirty state. Review, test, judge, and recovery
must not rely only on an agent's final message.

Diagnostics:

```bash
uv run zf doctor workdirs
uv run zf workdir repair dev-1
uv run zf refs verify
uv run zf doctor panes
```

## 7. Validate, Start, and Stop

Run a dry start before launching real workers:

```bash
uv run zf validate --cold-start
uv run zf skills doctor
uv run zf workflow inspect
uv run zf start --dry-run --no-watch
```

No topology `STOP` result means the deterministic startup path passed. It does
not prove provider login, network access, or a complete real delivery.

Start the harness:

```bash
uv run zf start
```

`zf start` runs the watcher in the foreground by default. Keep it running so
that events wake the orchestrator and periodic ticks can detect stuck workers,
orphaned tasks, context pressure, and recovery work.

Use `--no-watch` only when you intentionally want to spawn workers and exit.
`--foreground` is a deprecated no-op alias.

In another terminal:

```bash
uv run zf status --workers
uv run zf kanban --board
uv run zf events --last 30
uv run zf attach
```

Stop gracefully:

```bash
uv run zf stop
```

Use `zf stop --force` only when graceful shutdown cannot complete. Never use
`tmux kill-server`; it destroys unrelated sessions.

`zf stop --clean-workdirs` removes managed worktrees and branches. Before using
it, independently verify that every affected worktree is clean and its work is
merged or intentionally disposable. Do not use it as a generic recovery
command for dirty worktrees.

## 8. Submit and Manage Work

### 8.1 Natural-language goal

```bash
uv run zf chat "Implement a small feature with tests and review evidence."
```

This appends a `user.message` and lets the active workflow decide whether to
clarify, plan, create tasks, or dispatch work.

### 8.2 Deterministic task creation

```bash
TASK_ID="$(uv run zf kanban add "Fix session expiry" --id-only)"
uv run zf kanban assign "$TASK_ID" dev
uv run zf kanban show "$TASK_ID"
```

Use `zf kanban add` when the task is already concrete. Use `zf spec ingest` for
a structured document that should become multiple tasks.

```bash
uv run zf spec validate /path/to/spec.md --strict
uv run zf spec ingest /path/to/spec.md
```

### 8.3 Plan approval

When the workflow enables a human plan hold:

```bash
uv run zf plan review
uv run zf plan approve <plan_id>
# or
uv run zf plan reject <plan_id> --reason "scope is incomplete"
```

Approval is a controlled state transition. Agents must not approve their own
plan or bypass the hold with a hand-edited event.

### 8.4 Completion is evidence-gated

`zf kanban move <task_id> done` is not an unrestricted status write. The kernel
can reject it when review, test, judge, discriminator, contract, or artifact
evidence is missing.

When completion is rejected, inspect the trace and return the task to the
missing stage instead of editing `kanban.json`.

## 9. Observe a Run

Useful terminal commands:

| Command | Use |
|---|---|
| `zf status --workers` | Worker and role-session status |
| `zf kanban --board` | Active task board |
| `zf events --last 50` | Recent event facts |
| `zf watch --follow` | Live event stream |
| `zf task trace <task_id>` | Task causation chain |
| `zf backlog why-not-done <task_id>` | Explain missing completion conditions |
| `zf refs verify` | Task/candidate ref health |
| `zf runs explain` | Run/stage/attempt state |
| `zf metrics snapshot` | Long-horizon metrics |
| `zf cost --by-instance` | Cost by worker instance |

Feature-level delivery projections:

```bash
uv run zf trace delivery <feature_id>
uv run zf trace execution-graph <feature_id>
uv run zf trace drift <feature_id>
uv run zf trace task-node <task_id>
uv run zf trace workflow-run <fanout_id>
```

These commands are read-only projections. A drift report surfaces existing
facts; it does not replace kernel gates or mutate the run.

## 10. Web Dashboard

Install the Web extra and start the dashboard:

```bash
uv sync --extra web
uv run zf web --host 127.0.0.1 --port 8001
```

Use `0.0.0.0` only on a trusted network or for a controlled Docker test.

Main surfaces include:

- Inbox for operator attention;
- Channels for shared conversations;
- Tasks for Kanban and task inspection;
- Agents for worker, provider, context, and session state;
- Delivery, Trace, Graph, and Loop for execution evidence;
- Observability for events, failures, and diagnostics;
- Settings for action-token and runtime configuration status.

The Web/API layer is read-oriented. Mutating actions must use a trusted session,
passcode, or action token and are appended as auditable controlled actions.

For browser E2E, use the repository's Docker Playwright workflow rather than
installing browsers on the host.

## 11. Rework, Recovery, and Control Roles

### 11.1 Bounded rework

Review, test, judge, gate, and discriminator failures may route back to a
configured owner. `max_rework_attempts` prevents an unbounded retry loop.

Rework routing normally resolves in this order:

1. task contract override;
2. `workflow.rework_routing`;
3. workflow default.

### 11.2 Run Manager

Run Manager owns deterministic recovery decisions. It observes attempts,
leases, checkpoints, provider state, and expected downstream events, then plans
or applies controlled recovery actions according to policy.

### 11.3 Supervisor

Supervisor aggregates attention candidates, freshness, plan integrity,
failure signals, and operator-visible context. It does not replace `zf.yaml`,
dispatch tasks directly, kill workers, or apply repairs to the mainline.

There is currently no general `zf supervisor` command. Supervisor projections
are refreshed by the running watcher and stored under:

```text
<state_dir>/projections/supervisor/
```

### 11.4 Recovery commands

Use read-only diagnosis first:

```bash
uv run zf doctor
uv run zf state reconcile --dry-run
uv run zf recover workflow --help
uv run zf handoff --format state-packet --task <task_id>
```

Do not treat force-stop, hard reset, or worktree deletion as a normal recovery
mechanism.

## 12. Channels and Feishu

### 12.1 Channels

A Channel is an event-driven shared conversation. The stable CLI surface is
currently:

```bash
uv run zf channel say <channel_id> \
  --text "Review completed; please continue." \
  --member-id reviewer \
  --mention dev
```

Messages, mentions, streaming deltas, replies, and member lifecycle events are
projected from `channel.*` events.

Current limitations:

- general `channel list/show/invite/synth` CLI commands are not stable;
- advanced multi-speaker policy is still evolving;
- complex multi-provider collaboration must be verified against the current
  code and event chain.

### 12.2 Feishu direct bridge

Install the Feishu extra, configure credentials outside Git, and start the
in-process WebSocket bridge:

```bash
uv sync --extra feishu --extra stream-json

export FEISHU_APP_ID="cli_xxx"
export FEISHU_APP_SECRET="***"

uv run zf feishu bridge --watch --debounce-ms 600
```

Configure authorized routes and identities in the effective project config.
Supported route targets currently include `agent`, `channel`, `worker`, and
`kanban_agent`.

Feishu can carry messages, status cards, plan approvals, and controlled action
requests. It must not directly mutate kernel truth. Identity and approval
checks fail closed.

### 12.3 Read-only synchronization

Automation summaries and Kanban projections can be mirrored to Feishu Docx and
Bitable:

```bash
uv run zf feishu sync-automations --dry-run
uv run zf feishu sync-automation-insights-table --dry-run
uv run zf feishu sync-kanban-table --dry-run
```

This synchronization is one-way. Feishu documents and tables are not the ZaoFu
control plane.

## 13. Autoresearch and Real Evaluation

Autoresearch runs outside the harness under test. It prepares isolated test
worktrees, launches controlled scenarios, collects evidence, compares results,
and can propose repair work.

Start with a dry run:

```bash
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

Add `--confirm` only after checking provider login, branch/worktree isolation,
timeout, and budget. Add `--tmux` when an outer supervisor session is useful.

Important outputs are written under:

```text
<worktree>/<state_dir>/autoresearch/runs/<run-id>/
```

Read `report.md`, `events-summary.json`, and `inner-runner.log`. A passing shell
exit code alone is not sufficient. Confirm expected done count, zero fatal
signals, terminal evidence coverage, and scenario-specific assertions.

Autoresearch and self-repair are proposal-first. Applying a repair to the
mainline requires the explicit review/apply closeout path.

## 14. Verification Strategy

Use progressively more expensive validation:

| Level | Scope |
|---|---|
| L0 | Config, schema, skills, topology, and static preflight |
| L1 | Deterministic unit and integration tests |
| L2 | Scripted end-to-end workflow without a real provider |
| L3 | Single-provider real smoke |
| L4 | Multi-worker stress, recovery, and autoresearch scenarios |
| L5 | Web/API projection and browser verification |

Do not start with an expensive provider run when L0-L2 are red.

For ZaoFu Web E2E, start the API/UI on `0.0.0.0`, run
`mcp/playwright:latest` with host networking, and keep browser installation
inside Docker.

## 15. Troubleshooting

### Harness will not start

```bash
uv run zf validate --path zf.yaml
uv run zf validate --cold-start
uv run zf skills doctor
uv run zf workflow inspect
uv run zf doctor event-contract
```

### Worker does not progress

```bash
tmux ls
uv run zf status --workers
uv run zf events --last 100
uv run zf kanban --board
uv run zf task trace <task_id>
```

Common causes include a stopped watcher, missing trigger, stale dispatch token,
provider login/approval, context pressure, exhausted rework budget, or a failed
workdir preflight.

### Task cannot move to done

This normally means the completion gate is working. Inspect the task trace for
missing review, test, judge, discriminator, or artifact evidence.

### Workdir or candidate ref is unhealthy

```bash
uv run zf doctor workdirs
uv run zf refs verify
uv run zf workdir repair <instance_id>
```

Do not ask review/test/judge to evaluate an unpinned worker branch.

### Web returns 500 or shows the wrong project

Confirm the project root and state directory:

```bash
uv run zf web --state-dir /absolute/path/to/state-dir \
  --host 127.0.0.1 --port 8001
uv run zf runs rebuild
uv run zf state reconcile --dry-run
```

### Codex sandbox is unsupported

```bash
uv run zf doctor provider --backend codex --json
```

Repair host namespace/bubblewrap support when possible. Dangerous sandbox
bypass is appropriate only for an explicitly trusted, bounded local smoke and
must be recorded in the validation report.

### A tmux session is stuck

Operate on the exact configured session. Never use broad process matching and
never run `tmux kill-server` on a shared host.

## 16. Safety and Operational Boundaries

- Keep credentials in environment variables or ignored `.env` files.
- Do not print provider, Feishu, or action tokens in logs and reports.
- Keep `project.state_dir` out of source control.
- Treat Web, Feishu, Channels, and provider transcripts as projections or
  sidecars, not runtime truth.
- Require token-gated controlled actions for external mutations.
- Preserve dirty worktrees for inspection; do not force-remove them as routine
  cleanup.
- Use unique state directories, branch prefixes, tmux sessions, and ports for
  temporary E2E runs.
- Emit `simulation.done`, stop exact temporary sessions, and remove temporary
  resources after diagnosis.
- Keep real-provider budgets and timeouts explicit.
- Verify current command syntax with `uv run zf <command> --help` before
  scripting operational or destructive commands.

## 17. Command Map

The current top-level CLI includes these major groups:

| Area | Commands |
|---|---|
| Setup | `init`, `project`, `profile`, `config`, `presets`, `validate`, `preflight` |
| Runtime | `start`, `stop`, `restart`, `status`, `attach`, `logs`, `recover` |
| Work | `chat`, `feature`, `kanban`, `task`, `task-doc`, `spec`, `plan`, `issue` |
| Evidence | `emit`, `events`, `watch`, `trace`, `artifact`, `refs`, `handoff` |
| Quality | `gate`, `check`, `guard`, `rules`, `eval`, `metrics`, `failure` |
| Runtime state | `runs`, `archive-run`, `state`, `projection`, `report`, `cleanup` |
| Agents | `agents`, `skills`, `workdir`, `panes`, `ctx`, `memory` |
| Collaboration | `channel`, `web`, `feishu`, `bridge`, `workspace` |
| Improvement | `autoresearch`, `self-eval`, `self-repair`, `autopilot`, `bug-fix-cycle` |

Always use the current help for exact options:

```bash
uv run zf --help
uv run zf <command> --help
```

## 18. Further Reading

The [English manual index](00-index.en.md) links every topic-specific manual:

- [Architecture overview](architecture.en.md)
- [Quick start](01-quickstart.en.md)
- [`zf.yaml` control plane](02-zf-yaml-control-plane.en.md)
- [CLI operations](03-cli-operations.en.md)
- [Runtime lifecycle](04-harness-runtime.en.md)
- [Skills, workdirs, and Git evidence](05-skills-workdirs-git-evidence.en.md)
- [Web, observability, and E2E](06-web-observability-e2e.en.md)
- [Troubleshooting](07-troubleshooting.en.md)
- [New Task, Agent, and Squad](08-new-task-agent-squad.en.md)
- [Full CLI reference](09-zaofu-cli-usage.en.md)
- [Autoresearch](10-autoresearch-usage.en.md)
- [Feishu Automation and Kanban Sync](11-feishu-automation-kanban-sync.en.md)
- [Supervisor Inspection](12-supervisor-inspection-usage.en.md)
- [Plan, task map, and dispatch](13-plan-task-map-orchestrator-dispatch.en.md)
- [Delivery Trace](14-delivery-trace-usage.en.md)
- [Channel collaboration](15-channel-collaboration.en.md)
- [Codex provider preflight](16-real-codex-provider-preflight.en.md)
- [Product Fanout real E2E](18-product-fanout-real-e2e.en.md)
- [Feishu direct bridge](19-feishu-ai-native-direct-bridge.en.md)
- [Project Creation, Bootstrap, and Workflow Ignition](20-project-bootstrap-workflow-ignition.en.md)
- [Autoresearch Campaign](autoresearch-campaign.en.md)
- [Autoresearch Orchestrator](autoresearch-orchestrator.en.md)
