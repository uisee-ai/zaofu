# ZaoFu CLI Operations

> Audience: operators performing routine setup, runtime, task, event, gate,
> integration, and cleanup work.

Use the current help as the authoritative command contract:

```bash
uv run zf --help
uv run zf <command> --help
```

## 1. Initialization and Configuration

| Command | Purpose |
|---|---|
| `zf presets` | List presets |
| `zf presets show <name>` | Render a preset |
| `zf init` | Initialize configured runtime state |
| `zf init --preset safe-team` | Initialize from a preset |
| `zf project init --kind issue|prd|refactor ...` | Create a project workflow container |
| `zf profile detect` | Detect project stack |
| `zf profile recommend` | Recommend a profile |
| `zf validate --path zf.yaml` | Validate config |
| `zf validate --cold-start` | Check startup readiness |
| `zf workflow inspect` | Inspect topology and handoff wiring |
| `zf preflight --path zf.yaml` | Run static launch preflight |

## 2. Start and Stop

```bash
uv run zf start --dry-run --no-watch
uv run zf start
uv run zf status --workers
uv run zf stop
```

Useful commands:

| Command | Purpose |
|---|---|
| `zf attach [role]` | Attach to the configured tmux role |
| `zf logs [role] --tail 100` | Read role/harness logs |
| `zf restart [role]` | Restart the harness or one role |
| `zf stop --fast` | Scoped fast teardown |
| `zf stop --force` | Last-resort forced stop |

Do not use `tmux kill-server`. Do not use `--clean-workdirs` until affected
worktrees have been independently verified clean and disposable.

## 3. Tasks and Features

| Command | Purpose |
|---|---|
| `zf chat <message>` | Submit a natural-language goal |
| `zf feature add <title>` | Create a feature |
| `zf feature list` | List features |
| `zf kanban --board` | Display the board |
| `zf kanban add <title>` | Create a task |
| `zf kanban assign <task> <role>` | Assign a task |
| `zf kanban handoff ...` | Atomically update contract and next owner |
| `zf kanban move <task> <status>` | Request a status transition |
| `zf kanban show <task>` | Show task details |
| `zf task trace <task>` | Show task causation |
| `zf backlog why-not-done <task>` | Explain completion gaps |

Example:

```bash
TASK_ID="$(uv run zf kanban add "Add a regression test" --id-only)"
uv run zf kanban assign "$TASK_ID" dev
uv run zf task trace "$TASK_ID"
```

Strict terminal transitions require configured evidence. A rejected move to
`done` should be diagnosed, not bypassed by editing state files.

## 4. Events

| Command | Purpose |
|---|---|
| `zf events --last N` | Query recent events |
| `zf events --type TYPE` | Filter by type |
| `zf events trace <event_id>` | Show causation chain |
| `zf emit <type> --task <id> --payload JSON` | Append an authorized event |
| `zf watch --follow` | Follow event stream |
| `zf watch --task <id>` | Filter by task |
| `zf watch --role <role>` | Filter by actor/role |

Manual `emit` is useful for controlled diagnostics and worker reporting. It is
not a normal way to forge a missing terminal gate.

## 5. Skills

```bash
uv run zf skills list
uv run zf skills list --json
uv run zf skills doctor
uv run zf validate --strict-skills
```

Inspect missing, invalid, ambiguous, or unexpectedly shadowed skills before a
real run.

## 6. Workdirs, Refs, and Runs

| Command | Purpose |
|---|---|
| `zf doctor workdirs` | Workdir health |
| `zf doctor panes` | tmux pane binding health |
| `zf panes repair` | Non-destructive binding repair |
| `zf workdir repair <instance>` | Repair a configured workdir |
| `zf refs verify` | Verify task and candidate refs |
| `zf runs list` | List run projections |
| `zf runs rebuild` | Rebuild run projection |
| `zf runs reconcile` | Reconcile stale runs |
| `zf runs for-task <task>` | Runs for one task |
| `zf runs explain` | Explain run/stage/attempt state |
| `zf archive-run ...` | Archive live state |

Delivery trace:

```bash
uv run zf trace delivery <feature_id>
uv run zf trace execution-graph <feature_id>
uv run zf trace drift <feature_id>
uv run zf trace workflow-run <fanout_id>
```

## 7. Gates, Cost, and Metrics

```bash
uv run zf gate list
uv run zf gate run <name>
uv run zf gate run all
uv run zf cost --by-instance
uv run zf cost --by-backend
uv run zf metrics snapshot
uv run zf metrics diagnose
```

Query commands expose budget state; dispatch preflight performs the actual hard
budget enforcement.

## 8. Web and External Integrations

```bash
uv run zf web --host 127.0.0.1 --port 8001
uv run zf feishu bridge --watch
uv run zf channel say <channel_id> --text "hello"
uv run zf autoresearch --help
```

Web and integration mutations must use trusted, token-gated controlled action
paths. Bind `0.0.0.0` only on a trusted network or for controlled Docker E2E.

## 9. Runtime State Cleanup

Preview first:

```bash
uv run zf state clean --dry-run
```

After stopping the harness and preserving required evidence:

```bash
uv run zf state clean --confirm --archive
```

State cleanup targets rebuildable projections. Do not delete truth files or
dirty worktrees as routine cleanup.
