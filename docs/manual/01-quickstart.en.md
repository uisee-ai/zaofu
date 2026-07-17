# ZaoFu Quick Start

> Audience: first-time operators materializing a production Controller for an
> existing project and running the shortest safe end-to-end path.

This guide uses the product Controller catalog under
`examples/prod/controller/`. Generic presets are not the product workflow
entry point described here. Keep one project-local `zf.yaml` and one configured
`project.state_dir`; later PRD, issue, feature, or refactor work enters that
same project as a new workflow request.

## 0. Before You Start

Required:

- Python 3.11+
- `uv`
- Git
- `tmux`
- at least one authenticated provider CLI: `codex` or `claude`

From the ZaoFu source checkout:

```bash
cd /path/to/zaofu
uv sync --extra dev --extra web --extra stream-json
uv run zf --version
```

`stream-json` is required for Claude Code stream-json transports. `web` is
needed only for the local dashboard.

Check the provider before starting real workers:

```bash
command -v tmux
command -v codex      # when using --backend codex
command -v claude     # when using --backend claude

uv run zf doctor provider --backend codex
```

Provider authentication is external to ZaoFu. Resolve missing binaries or
login failures before continuing.

## 1. Inspect the Controller Recommendation

Set stable source and target paths:

```bash
export ZAOFU_ROOT=/path/to/zaofu
export TARGET_PROJECT=/path/to/my-project
```

Inspect the recommendation without writing files:

```bash
uv run --project "$ZAOFU_ROOT" zf profile bootstrap \
  "$TARGET_PROJECT" \
  --intent build \
  --backend codex \
  --scale launch
```

Intent selects the product workflow family:

| Intent | Typical Controller family |
|---|---|
| `build` | PRD delivery (`prd-fanout-v3` or the light variant) |
| `refactor` | lane-based refactor (`refactor-lane-v3`) |
| `maintain` / `review` | issue and regression flow (`issue-fanout-v3`) |

The output is an approval point. For this product path, continue only when
`archetype` is a `[flow]` entry from `examples/prod/controller/`. A recommendation
marked `[preset]` is a generic fallback, not the production Controller catalog.

For a greenfield project with too little code to classify, use the Web New
Project wizard to select a Controller explicitly, then provide real
project-specific quality checks.

## 2. Materialize and Review the Controller

After approving the recommendation:

```bash
uv run --project "$ZAOFU_ROOT" zf profile bootstrap \
  "$TARGET_PROJECT" \
  --intent build \
  --backend codex \
  --scale launch \
  --apply
```

Materialization writes the selected Controller as project-local `zf.yaml` and
copies required profile and skill assets. `zf.yaml` remains the only active
control-plane configuration.

If `zf.yaml` already exists, bootstrap preserves it and only fills detectable
checks without clobbering existing values. It does not silently switch an
existing project to another Controller. Review or migrate the current control
plane deliberately before continuing.

Before startup, review:

- Controller inputs such as `prdRef`, `issueRef`, `sourceRoot`, or `targetRoot`;
- `project.state_dir` and worktree policy;
- provider backend and permission policy;
- `quality_gates` commands against the actual target project;
- placeholders or missing environment requirements reported by validation.

Bootstrap can fill detectable checks, but it cannot invent product semantics
or acceptance criteria. Multi-lane Controllers fail closed when required
project quality gates are absent.

## 3. Initialize, Validate, and Dry Run

Run project commands from the target project so relative paths resolve against
its `zf.yaml`:

```bash
cd "$TARGET_PROJECT"

uv run --project "$ZAOFU_ROOT" zf init \
  --workspace-register \
  --with-bootstrap

uv run --project "$ZAOFU_ROOT" zf validate --cold-start
uv run --project "$ZAOFU_ROOT" zf skills doctor
uv run --project "$ZAOFU_ROOT" zf workflow inspect
uv run --project "$ZAOFU_ROOT" zf start --dry-run --no-watch
```

Do not start real providers while validation reports a `STOP`. Fix missing
routes, skills, gates, inputs, or tools, then rerun the same checks. A dry run
validates deterministic startup wiring; it does not prove provider login or
product delivery quality.

## 4. Start and Observe

Start the watcher and workers:

```bash
uv run --project "$ZAOFU_ROOT" zf start
```

The watcher runs in the foreground by default. Keep it alive. In another
terminal:

```bash
cd "$TARGET_PROJECT"
uv run --project "$ZAOFU_ROOT" zf status --workers
uv run --project "$ZAOFU_ROOT" zf kanban --board
uv run --project "$ZAOFU_ROOT" zf events --last 30
uv run --project "$ZAOFU_ROOT" zf attach
```

## 5. Submit Work

For an initial operator goal:

```bash
uv run --project "$ZAOFU_ROOT" zf chat \
  "Implement a small feature with tests, review, and delivery evidence."
```

For a typed product request, create an intake artifact first. This PRD example
uses the stock PRD route:

```bash
uv run --project "$ZAOFU_ROOT" zf flow intake \
  --kind prd \
  --from docs/prd/account-security.md \
  --target-root app \
  --request-id prd-account-security \
  --output docs/intake/prd-account-security.md
```

Preview admission without mutating runtime state:

```bash
uv run --project "$ZAOFU_ROOT" zf flow submit \
  --dry-run \
  --config zf.yaml \
  --intake docs/intake/prd-account-security.md \
  --kind prd \
  --pattern-id prd-scan \
  --allow-missing-env \
  --json
```

After reviewing the preview and resolving environment requirements:

```bash
uv run --project "$ZAOFU_ROOT" zf flow submit \
  --apply \
  --config zf.yaml \
  --intake docs/intake/prd-account-security.md \
  --kind prd \
  --pattern-id prd-scan \
  --json
```

Only request kinds declared in `workflow.kind_routes` are admitted. Projects
that set the route pattern in `workflow.kind_routes` can omit `--pattern-id`.
Use a new `request_id` for later work; do not create a second control plane for
the same project.

## 6. Optional Dashboard

From the ZaoFu checkout:

```bash
tools/start-webkanban.sh --host 127.0.0.1 --port 8001
```

Open `http://127.0.0.1:8001/`. Web mutations require the generated or supplied
action token. Bind `0.0.0.0` only on a trusted network.

## 7. Stop

```bash
uv run --project "$ZAOFU_ROOT" zf stop
```

Use `zf stop --force` only when graceful shutdown cannot complete. Never run
`tmux kill-server` on a shared host; stop only the project session.

## Next

- [Architecture Overview](architecture.en.md)
- [CLI Operations](03-cli-operations.en.md)
- [Web, Observability, and E2E](06-web-observability-e2e.en.md)
- [Troubleshooting](07-troubleshooting.en.md)
- [Autoresearch](10-autoresearch-usage.en.md)
- [Feishu AI-Native Direct Bridge](19-feishu-ai-native-direct-bridge.en.md)
