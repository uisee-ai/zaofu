# ZaoFu Quick Start

> Audience: first-time operators starting ZaoFu in a project or validating a
> source checkout with the shortest safe path.

## 0. Preflight

Before starting real agents, check the provider CLI and transport required by
the active config:

```bash
command -v tmux
command -v claude    # backend: claude-code
command -v codex     # backend: codex

uv run zf doctor provider --backend codex
claude --version
uv run zf --version
```

Provider authentication is also required. A missing CLI or login may leave a
worker pane alive but unable to execute useful work.

## 1. Enter the Source Checkout

Use `uv` for a development checkout:

```bash
cd /path/to/zaofu
uv sync --extra dev --extra stream-json
uv run zf --version
```

Add optional integrations when needed:

```bash
uv sync --extra dev --extra web --extra stream-json --extra feishu
```

`stream-json` is required for the Claude Code stream-json transport. `web` and
`feishu` install the dashboard and direct Feishu bridge dependencies.

## 2. Create or Inspect `zf.yaml`

`zf.yaml` is the only workflow control-plane config. Inspect an existing file
before generating a replacement.

```bash
uv run zf presets
uv run zf presets show safe-team
uv run zf init --preset safe-team
```

Current presets include `safe-team`, `safe-local`, `minimal`, `code-assist`,
and `design-first`. Their topology evolves with the implementation, so always
run current validation and workflow inspection.

To test a preset, use a fresh project. Running `zf init --preset` inside an
existing configured project does not make old project constraints disappear.

## 3. Bootstrap a New Project

The preferred path is:

```bash
tools/init-project.sh \
  --project-dir /path/to/project \
  --preset safe-team \
  --yes
```

With an existing config:

```bash
tools/init-project.sh \
  --project-dir /path/to/project \
  --source-config /path/to/project/zf-codex.yaml \
  --yes
```

The bootstrap prepares the config, initializes state, creates project
instructions when missing, registers the workspace when requested, validates
Git/worktree prerequisites, and runs a dry startup check.

## 4. Initialize Runtime State

```bash
uv run zf init
```

Useful options:

```bash
uv run zf init /path/to/project --create --preset safe-team
uv run zf init --workspace-register --with-bootstrap
uv run zf init --skip-instruction-docs
uv run zf init --env-check
uv run zf init --no-git-hooks
```

Initialization creates the configured `project.state_dir`, normally `.zf/`,
and can create or refresh `AGENTS.md` and `CLAUDE.md`.

`zf init --force` reinitializes runtime truth. Archive required evidence before
using it.

## 5. Validate Before Launch

```bash
uv run zf validate --path zf.yaml
uv run zf validate --cold-start
uv run zf validate --strict-skills
uv run zf skills doctor
uv run zf workflow inspect
uv run zf gate list
```

Fix topology `STOP` findings before starting a real provider. Typical findings
identify an event without a producer, a missing rework route, an orphan stage,
or a missing skill.

## 6. Dry Run

```bash
uv run zf start --dry-run --no-watch
```

A dry run exercises startup preparation without launching real tmux workers.
It validates the deterministic wiring, not provider authentication or actual
delivery quality.

## 7. Start the Real Harness

```bash
uv run zf start
```

The watcher runs in the foreground by default. Keep that process alive. In a
second terminal:

```bash
uv run zf status --workers
uv run zf kanban --board
uv run zf events --last 20
uv run zf attach
```

Use the configured `session.tmux_session` when attaching directly with tmux.

## 8. Submit Work

Natural-language goal:

```bash
uv run zf chat "Implement a small feature with tests, review, and final evidence."
```

Deterministic task creation:

```bash
TASK_ID="$(uv run zf kanban add "Fix a concrete bug and add a regression test" --id-only)"
uv run zf kanban assign "$TASK_ID" dev
uv run zf kanban show "$TASK_ID"
```

Strict workflows may reject assignment or completion when required evidence is
missing. This is expected gate behavior.

## 9. Stop

```bash
uv run zf stop
```

Use `zf stop --force` only if graceful shutdown cannot complete. Never use
`tmux kill-server` on a shared host. Operate only on the exact session declared
by the project.

Next: [Architecture Overview](architecture.en.md),
[`zf.yaml` Control Plane](02-zf-yaml-control-plane.en.md), and
[Troubleshooting](07-troubleshooting.en.md).
