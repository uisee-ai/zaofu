# `zf.yaml` Control Plane and Runtime State

> Audience: operators configuring roles, skills, workdirs, gates, budgets, and
> recovery behavior.

## 1. Core Principle

`zf.yaml` is ZaoFu's canonical control-plane configuration. External systems
may submit intent through events, the CLI, or controlled APIs, but they must not
write business truth or introduce a second task schema.

Minimal shape:

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
    permission_mode: bypass
    triggers: [task.assigned]
    publishes: [dev.build.done]
```

## 2. Top-Level Groups

| Group | Purpose |
|---|---|
| `version` | Config version |
| `project` | Project identity and `state_dir` |
| `session` | tmux session and layout |
| `orchestrator` | Layer 2 provider and turn policy |
| `roles` | Worker roles and event contracts |
| `providers` | Provider-specific bindings |
| `integrations` | Feishu and external adapters |
| `skill_sources` | Read-only skill source roots |
| `runtime` | Workdirs, Git isolation, skills, Run Manager |
| `workflow` | Stages, pipelines, fanout/fanin, rework and wake policy |
| `quality_gates` | Deterministic shell-command gates |
| `verification` | Contract, scope, architecture, and promoted checks |
| `autoresearch` | Trigger, review, resident, and repair policy |
| `goal` | Goal and evaluation policy |
| `security` | Signing and security options |
| `global_budget_usd` | Global budget ceiling |

The current schema, loader, CLI help, and runtime callers define implemented
behavior. Design documents may also contain future intent.

## 3. Role Configuration

Common fields:

| Field | Meaning |
|---|---|
| `name` | Logical role, such as `dev`, `review`, or `judge` |
| `backend` / `backends` | Provider backend, optionally per replica |
| `model` | Provider model override; empty uses provider default |
| `permission_mode` | Provider permission posture |
| `allowed_tools` | Tool allowlist when applicable |
| `transport` | Usually `tmux`; `stream-json` is supported on selected paths |
| `replicas` | Static role replica count |
| `role_kind` | `writer`, `reader`, or `auto` |
| `skills` | Enabled skill names |
| `triggers` | Events that make the role eligible |
| `publishes` | Events the role is authorized to publish |
| `stuck_threshold_seconds` | Stuck detection threshold |
| `orphan_warning_seconds` | Orphan warning threshold |
| `orphan_escalate_seconds` | Orphan escalation threshold |
| `max_rework_attempts` | Bounded rework cap |
| `context_*` | Context warning, compact, and hard-cap policy |
| `budget_usd` | Per-role budget |

Prefer replicas over duplicated role definitions. A logical `dev` role with
four replicas expands to concrete worker instances such as `dev-1` through
`dev-4`.

## 4. Skills

```yaml
skill_sources:
  - name: agent-skills
    path: ${ZF_AGENT_SKILLS_DIR:-/path/to/agent-skills}
    mode: readonly
  - name: zaofu-local
    path: ${ZF_ZAOFU_SKILLS_DIR:-/path/to/zaofu/skills}
    mode: readonly

runtime:
  skills:
    pool: .zf/skills
    materialize: copy
    lock_file: .zf/skills.lock.json
    strict: false
```

Roles declare only the skills they need. ZaoFu resolves source candidates,
detects conflicts, materializes skills into role runtime contexts, and records
the result in a lock/projection.

## 5. Workdirs and Git Isolation

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

Recommended defaults:

| Role | Kind | Reason |
|---|---|---|
| dev | writer | Produces source changes in isolation |
| review | reader | Reviews a pinned candidate |
| test | reader | Verifies candidate state independently |
| judge | reader | Evaluates terminal evidence |
| orchestrator | auto | Coordinates rather than implementing |

## 6. Quality Gates and Verification

Command gates:

```yaml
quality_gates:
  static:
    enabled: true
    required_checks:
      - PYTHONPATH=src pytest -q
      - npm --prefix web test
```

Deterministic verification:

```yaml
verification:
  contract:
    required: true
    quality_required: true
    rework_delta_required: true
    dispatch_token_required: true
  scope:
    fail_closed: true
  architecture:
    enabled: true
  promoted:
    enabled: true
```

Gates answer whether commands pass. Discriminators answer whether evidence,
scope, contract, and architecture requirements are satisfied.

## 7. Runtime State Files

The configured state directory typically contains:

| Path | Classification |
|---|---|
| `events.jsonl` | append-only truth |
| `kanban.json` | active task truth |
| `feature_list.json` | active feature truth |
| `session.yaml` | harness session truth |
| `role_sessions.yaml` | role/provider session truth |
| `cost.jsonl` | cost ledger/projection input |
| `skills.lock.json` | rebuildable skill resolution record |
| `instructions/` | generated role briefings/instructions |
| `workdirs/` | managed workdirs and checkouts |
| `runs/` | run archives |
| `projections/` | rebuildable Web and diagnostic views |
| `fanouts/` | fanout result sidecars and manifests |

Do not hand-edit truth files. Use kernel stores and event helpers.

## 8. Compatibility Guidance

- Some older field names are accepted for migration, but new configs should
  use the current schema.
- Environment variables only affect behavior when referenced from `zf.yaml` or
  explicitly consumed by the relevant adapter.
- Validate the rendered/effective config before a real run.
- Use `uv run zf <command> --help` before scripting destructive operations.
