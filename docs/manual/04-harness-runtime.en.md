# Harness Runtime

> Audience: operators who need to understand how ZaoFu advances a goal through
> implementation, review, verification, judgment, and completion.

## 1. Three Layers

| Layer | Responsibility | Examples |
|---|---|---|
| Layer 1 deterministic kernel | Events, state, gates, dispatch, stuck/orphan detection | `src/zf/core/`, `src/zf/runtime/` |
| Layer 2 orchestrator | Goal decomposition, routing, rework and replan decisions | orchestrator/controller roles |
| Layer 3 workers | Architecture, implementation, review, test, judge | `arch`, `dev`, `review`, `test`, `judge` |

Kernel code owns truth and mechanical transitions. Agents express judgment via
events, artifacts, and controlled CLI actions.

## 2. Task Chain

An illustrative code task may follow:

```text
user.message
  -> task.created
  -> task.assigned(dev)
  -> dev.build.done
  -> review.approved or review.rejected
  -> test.passed or test.failed
  -> judge.passed or judge.failed
  -> discriminator.passed
  -> done
```

Failures route according to the task contract and `workflow.rework_routing`.
The active config may instead use plan stages, static gates, fanout/fanin,
`impl.child.*`, `verify.child.*`, or product-flow events.

## 3. Preventing Premature Completion

ZaoFu does not accept a worker's final sentence as completion. A terminal task
must satisfy the configured stage chain, contract, gate, evidence, scope, and
discriminator requirements.

`zf kanban move <task> done` uses the same principle. Missing review, test,
judge, or discriminator evidence can cause a deterministic rejection event.

## 4. Contract and Dispatch Token

A strict task contract may carry:

- target behavior;
- verification method;
- quality expectations;
- out-of-scope boundaries;
- acceptance criteria;
- rework delta;
- artifact and source references;
- the active dispatch identity.

Completion evidence must match the active dispatch. This prevents a stale
session or replayed success event from closing a newer attempt.

## 5. Watcher and Wake Patterns

`zf start` runs an `EventWatcher` in the foreground. It wakes the orchestrator
for configured events and performs periodic ticks for recovery and projections.

```bash
uv run zf events --last 30
uv run zf watch --follow
uv run zf status --workers
```

Using `--no-watch` means the long-running watcher is intentionally absent.
Workers may finish while downstream stages remain undispatched.

## 6. Stuck, Orphan, and Recovery

| Condition | Detection | Response |
|---|---|---|
| Stuck worker | stale output, heartbeat, or activity | recovery signal, restart or redispatch by policy |
| Orphaned task | in-progress without stage progress | warning, escalation, possible requeue |
| Context pressure | provider context threshold | checkpoint, compact/recycle, or dispatch hold |

Example role policy:

```yaml
stuck_threshold_seconds: 180
orphan_warning_seconds: 300
orphan_escalate_seconds: 600
context_window_tokens: 200000
context_warning_threshold: 0.60
context_compact_threshold: 0.70
context_hard_cap: 0.90
```

Environment interpolation changes runtime behavior only when the config or
adapter explicitly consumes the variable.

## 7. Bounded Rework

```yaml
max_rework_attempts: 3
```

Repeated failure beyond the cap becomes an escalation/dead-letter condition
rather than an infinite loop. Routing normally resolves from task contract,
then workflow policy, then the workflow default.

## 8. Agent Telemetry

Provider hooks and session tailers project tool calls, text, usage, and status
into `agent.*` events. Telemetry is evidence and observability, not a second
state machine.

Codex may require explicit hook review. A missing review can leave the worker
running while hook-derived evidence remains absent.

## 9. Gates and Discriminators

- A quality gate runs deterministic commands.
- A discriminator checks contract, evidence, scope, architecture, and policy.

It is valid for `judge.passed` to be followed by `discriminator.failed` in a
strict workflow. The second check answers a different question.

## 10. Run Manager, Supervisor, and Autoresearch

- Run Manager owns deterministic recovery decisions and post-action
  verification.
- Supervisor aggregates health, freshness, plan integrity, and attention
  candidates; it does not directly rewrite truth.
- Autoresearch performs deeper, bounded diagnosis and can produce repair
  candidates. Mainline application remains gated.

## 11. Long-Run Acceptance

Do not accept a run based only on the last agent message. Check:

```bash
uv run zf kanban --board
uv run zf task trace <task_id>
uv run zf refs verify
uv run zf runs explain --task <task_id>
uv run zf metrics snapshot
uv run zf doctor
```

Acceptance requires a terminal task/feature, traceable terminal evidence, no
unresolved fatal condition, identifiable Git evidence, and the required test
or Web/API verification.
