# `zf.yaml` Control Plane and Runtime State

> Audience: operators configuring roles, skills, workdirs, gates, budgets, and
> recovery behavior.

## 1. Core Principle

`zf.yaml` is ZaoFu's canonical control-plane configuration. External systems
may submit intent through events, the CLI, or controlled APIs, but they must not
write business truth or introduce a second task schema.

### 1.1 Why Project creation no longer asks for YAML

Web `Add/Open Project` no longer asks the operator to select YAML, a Controller,
kind, lane, or role. This simplifies admission; it does not remove `zf.yaml`:

- when the directory already has a valid `zf.yaml`, Inspect chooses only open,
  register, or initialize-state behavior and preserves the configuration;
- when no configuration exists, Initialize generates one default multi-kind
  `zf.yaml`;
- Project Brief, Stack, and Provider enrich project context, command Profile,
  and provider policy; they do not select a Workflow;
- when Kanban Agent recommends a Workflow for an existing Task, it may use only
  routes expanded from that Project's current `zf.yaml`.

Each Project therefore keeps one canonical `zf.yaml`. Use
`zf profile bootstrap` or an explicit migration when adopting a single
Controller or changing an existing control plane, with operator review before
write. Do not put YAML selection back into normal Project admission.

Reduced low-level `ZfConfig` shape (without Flow document/profile expansion):

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
  - name: orchestrator
    backend: claude-code
    role_kind: reader
    triggers: [dispatch.silent_stall, orchestrator.rework.triage.requested]
    publishes: [orchestrator.rework.triage.recorded]
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
| `orchestrator` | Python runtime loop plus compatibility Agent-wake backend/turn/timeout policy; it does not determine authority by itself |
| `roles` | Worker roles and explicit exception-advisor roles |
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

In Product Flow, the Kernel mechanically dispatches from topology/profile. An
explicit `orchestrator` role subscribes to exceptional triage by default; only
`workflow.orchestration.mode: semantic_control` lets it make shadow/blocking
semantic judgments at registered checkpoints. Legacy safe-team remains a
separate compatibility mode. Treat the `orchestrator` config block, Python
`WorkflowRuntimeCoordinator` (`Orchestrator` compatibility alias), and the
same-named role Agent as three distinct objects.

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
| `provider_session` | Role-scoped Provider-native `effort`, `agent`, and `max_parallel_agents`, frozen by digest |
| `lifecycle` | Provider process/pane lifecycle: `eager`, `resident`, or `on_demand` |
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

Role-level `constraints.blocked_paths` and `constraints.max_steps` currently provide
method instructions to the agent, without a dedicated write interceptor or step counter.
Codex has no native equivalent of the `allowed_tools` allowlist. Task/operation scope
hooks, controlled-action authorization, and admitted operation/run budgets have separate
mechanical consumers; these controls do not provide an OS sandbox for arbitrary shell
commands. A configured or delivered field alone is not proof of enforcement.

### 3.1 Role-Scoped Provider Sessions and On-Demand Lifecycle

Initial Provider dynamic configuration belongs to a role/session rather than a
per-Task execution-profile selector:

```yaml
roles:
  - name: orchestrator
    backend: codex
    role_kind: reader
    lifecycle:
      mode: resident
  - name: dev-lane-0
    backend: codex
    role_kind: writer
    provider_session:
      effort: high
      max_parallel_agents: 2
    lifecycle:
      mode: on_demand
      idle_seconds: 120
      cooldown_seconds: 30
      preserve_session: true
      preserve_workdir: true
```

When `provider_session` is absent, the Provider default is inherited. Explicit
settings produce an immutable effective-session sidecar whose digest binds the
`RoleSessionRegistry`; a digest change cannot blindly resume an old session.
`on_demand` releases only an idle process/pane while retaining logical role,
session, workdir, and affinity. The `orchestrator` role cannot be `on_demand`.
Broad real-Provider rollout is still pending, so use this first in controller
canaries.

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
| orchestrator | reader / auto | A Product Flow exception advisor does not implement or own the happy-path state machine |

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

### 6.1 OA Checkpoints and the Task Pipeline v4 Canary

OA semantic checkpoints require explicit configuration; the default remains
`exception_advisor`:

```yaml
workflow:
  orchestration:
    mode: semantic_control
    checkpoints: [plan_candidate]
    checkpoint_policies:
      plan_candidate: shadow
```

`shadow` records a typed decision without blocking the Kernel. `blocking` is
limited to PRD/Issue/Refactor profiles with an explicit pilot/canary boundary.
The current real canary is on HOLD and is not a Project default.

Task Pipeline v4 is also enabled only by an explicit Flow document:

```yaml
apiVersion: zaofu.dev/v1
kind: IssueFlow
spec:
  flowProfile: issue-flow-v4-task-pipeline
  topology: fanout
  taskPipeline:
    mode: ${ZF_TASK_PIPELINE_MODE:-shadow}
    maxActiveTaskPipelines: 4
    pools:
      impl: {capacity: 1, roleInstances: [fix-lane-0]}
      verify: {capacity: 1, roleInstances: [verify-lane-0]}
    workerLifecycle: {mode: on_demand, idleSeconds: 120}
    integrationAdmission:
      default: verify_admitted
      riskReview: {enabled: false}
    candidate:
      integration: incremental_serial_cas
      integrationCapacity: 1
      rollingSmoke: required
      partialCandidateAutoShip: forbidden
      finalVerifyTarget: frozen_exact_commit
```

The only valid profile IDs are `issue-flow-v4-task-pipeline`,
`prd-flow-v4-task-pipeline`, and `refactor-flow-v4-task-pipeline`. Repository
canaries live under
`examples/prod/controller/*-task-pipeline-v4-canary*.yaml` and all declare
`preferred: false`. The implementation is complete but rollout remains NO-GO.
Do not interpret `shadow` as owning business dispatch, and never hot-edit its
mode/profile during an active Run.

## 7. Runtime State Files

The configured state directory typically contains:

| Path | Classification |
|---|---|
| `events.jsonl` | append-only occurrence/order/causation/verdict/ref ledger |
| `kanban.json` | canonical active Task current state |
| `feature_list.json` | canonical active Feature current state |
| `session.yaml` | canonical harness session current state |
| `role_sessions.yaml` | canonical role/provider session mapping |
| `cost.jsonl` | cost ledger/projection input |
| `skills.lock.json` | rebuildable skill resolution record |
| `instructions/` | generated role briefings/instructions |
| `workdirs/` | managed workdirs and checkouts |
| `runs/` | run archives |
| `projections/` | rebuildable Web and diagnostic views |
| `fanouts/` | fanout result sidecars and manifests |

Do not hand-edit canonical state or ledgers. Use Kernel stores, event helpers, artifact writers, and controlled actions.

## 8. Compatibility Guidance

- Some older field names are accepted for migration, but new configs should
  use the current schema.
- Environment variables only affect behavior when referenced from `zf.yaml` or
  explicitly consumed by the relevant adapter.
- Validate the rendered/effective config before a real run.
- Use `uv run zf <command> --help` before scripting destructive operations.
