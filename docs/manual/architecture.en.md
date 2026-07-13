# ZaoFu Architecture Overview

> Audience: anyone who wants to understand how ZaoFu turns a goal into a
> verified delivery. Read this before the operational manuals.

## 1. What ZaoFu Is

ZaoFu is a multi-agent engineering harness. A user provides a goal, bug, PRD,
or refactor request. A configurable team of agents plans, implements, reviews,
tests, and judges the result while a deterministic kernel records and governs
the process.

ZaoFu is designed to keep long-running multi-agent work under control:

- an agent cannot close work with a chat claim alone;
- source scope and protected paths can be enforced;
- stuck workers and orphaned tasks are observable and recoverable;
- rework is bounded;
- cost can be capped;
- every important transition has event and Git evidence.

## 2. Three-Layer Architecture

ZaoFu separates deterministic mechanics from model judgment.

| Layer | Role | Responsibility | Property |
|---|---|---|---|
| Layer 1 | deterministic kernel | Parse events, update state, enforce gates, prepare briefings, detect failure and recovery conditions | Python, testable, no LLM judgment |
| Layer 2 | orchestrator agent and controllers | Decompose goals, choose routes, coordinate rework and replan | Agent judgment expressed through events and controlled actions |
| Layer 3 | worker agents | Architecture, implementation, review, verification, and final evaluation | Provider-backed workers in bounded execution contexts |

```text
User goal
   |
   v
Layer 2 orchestrator: understand, decompose, route
   |
   v
Layer 1 kernel: validate, dispatch, gate, persist
   |
   v
Layer 3 workers: plan, code, review, test, report evidence
   |
   +---------------- events and artifacts ----------------+
```

The central discipline is simple: agents do not directly edit runtime truth.
They emit facts and intent through the CLI or a sanctioned action path.

## 3. Two Foundations: Runtime Truth and One Control Plane

### 3.1 Kernel-managed truth

The configured `project.state_dir` contains the runtime truth and its
rebuildable projections. The default is `.zf/`, but code and integrations must
respect the configured path.

Primary truth includes:

| File | Purpose |
|---|---|
| `events.jsonl` | Append-only event history |
| `kanban.json` | Active task state |
| `feature_list.json` | Active feature state |
| `session.yaml` | Harness session state |
| `role_sessions.yaml` | Role-instance/provider-session bindings |

Web models, logs, skill materialization, transcripts, reports, and most files
under `projections/` are rebuildable views or sidecars.

### 3.2 `zf.yaml` as the control plane

`zf.yaml` declares roles, providers, triggers, publications, workflow stages,
gates, budgets, workdirs, skills, integrations, and recovery policy. External
systems may submit intent through events or controlled APIs; they may not
become an independent source of task or workflow truth.

## 4. Core Technical Concepts

### 4.1 Event sourcing and observability

State-changing facts are appended to `events.jsonl`. Events carry identity,
actor, task, causation, correlation, timestamp, and payload fields. The event
stream supports replay, trace reconstruction, recovery, Web projections, and
post-mortem analysis.

### 4.2 Task model and contracts

A Kanban task is not only a title. A strict `TaskContract` can define behavior,
verification, quality expectations, scope, non-goals, evidence, rework delta,
dependencies, and source artifacts.

The contract is injected into the worker briefing and checked again at
handoff and completion.

### 4.3 State machines and gates

An illustrative lifecycle is:

```text
backlog -> in_progress -> review -> test -> judge -> done
```

The configured workflow may be linear, fanout/fanin, plan-first, or product
flow based. Two kinds of checks cooperate:

- quality gates run deterministic commands;
- discriminators verify contract, evidence, scope, architecture, and policy.

An agent saying "done" is not a terminal condition. A valid event chain and
supporting evidence are required.

### 4.4 Constraints and safety

Depending on the active configuration, ZaoFu can enforce protected paths,
tool closure, event signing, scope ratchets, secret controls, and budget gates.
These controls are configuration-dependent. Do not assume the strongest safety
profile is enabled without checking `zf.yaml` and preflight output.

### 4.5 Long-horizon resilience

The runtime observes:

| Risk | Signal | Response |
|---|---|---|
| Stuck worker | no useful pane/session progress | signal, recover, restart, or redispatch according to policy |
| Orphaned task | in-progress task without stage progress | warning, escalation, and possible requeue |
| Context pressure | provider context crosses thresholds | checkpoint, compact/recycle, or block new dispatch |

`max_rework_attempts` prevents endless failure loops. Checkpoints, state
packets, event replay, and Git evidence let a fresh session continue work.

### 4.6 Event-driven orchestration

`zf start` runs an `EventWatcher`. Wake-worthy events invoke the orchestrator;
periodic ticks run health, recovery, projection, and policy services even when
the event stream is quiet.

Mechanical transitions stay in Layer 1. The runtime invokes Layer 2 only when
semantic judgment is needed.

### 4.7 Isolation and evidence

With worktree mode enabled, writer roles use isolated Git worktrees and reader
roles inspect pinned refs. Task refs provide handoff boundaries, candidate refs
provide an integrated view, and Git evidence identifies base, head, diff,
changed paths, and verification results.

### 4.8 Cost and metrics

Provider usage is projected into cost and metric views. Role and global budget
limits can block new dispatches. `zf cost` and `zf metrics snapshot` make the
result visible to operators.

## 5. End-to-End Task Lifecycle

```text
user.message
  -> feature or task creation
  -> task assignment
  -> implementation evidence
  -> review verdict
  -> test verdict
  -> judge verdict
  -> discriminator verdict
  -> done or bounded rework
```

Failure routing follows task contract and workflow policy. Existing completed
work is not silently rewritten; corrections should be represented by new facts
and, when necessary, correction tasks.

## 6. Runtime Directory

Typical state layout:

```text
<state_dir>/
  events.jsonl
  kanban.json
  feature_list.json
  session.yaml
  role_sessions.yaml
  cost.jsonl
  artifacts/
  fanouts/
  runs/
  projections/
  workdirs/
  logs/
```

The state directory is not source code and should not be committed. Mutate
truth only through ZaoFu commands and kernel helpers.

## Next Steps

- [Quick Start](01-quickstart.en.md)
- [`zf.yaml` Control Plane](02-zf-yaml-control-plane.en.md)
- [Harness Runtime](04-harness-runtime.en.md)
- [CLI Operations](03-cli-operations.en.md)
