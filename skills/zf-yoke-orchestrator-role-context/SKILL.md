---
name: zf-yoke-orchestrator-role-context
description: "Use for ZaoFu orchestrator roles that need yoke-style harness discipline without loading duplicate yoke baseline skills."
---

# ZaoFu Yoke Orchestrator Role Context

Local adaptation of yoke orchestrator role context for ZaoFu.

## Rules

- Understand the request before dispatching work.
- Do not forward ambiguity downstream as "figure it out".
- Dispatch only concrete tasks with goal, context, steps, scope, verification,
  and completion protocol.
- Every dispatched task in strict mode must include a complete contract:
  `behavior`, `verification`, non-empty `verification_tiers`
  (`static`, `runtime`, `e2e`, or `manual_evidence`), and `owner_role` or
  `owner_instance`. Missing fields trigger `task.contract.invalid` and Layer 1
  will not dispatch the worker.
- For parallel work, include wave, owner, blocked_by, shared/exclusive files,
  acceptance, verification, and handoff artifacts. Do not duplicate one atomic
  task across multiple workers.
- For fanout / affinity-lane work, synthesize lane contracts before dispatch:
  each task-map item must carry `task_id`, `affinity_tag`, `wave` when relevant,
  `allowed_paths`, `exclusive_files` or a serialization reason, dependencies,
  verification, and handoff evidence. Attach the
  `zf-harness-lane-goal-continuation` discipline so each lane completes only
  its assigned slice and cannot claim feature/product done.
- In topologies with `arch` plus `critic`, dispatch fresh user-message tasks to
  `arch` first; do not bypass the design gate by jumping directly to a
  downstream worker. Let `arch.proposal.done` and `design.critique.done` drive
  the gate before backlog / implementation routing.
- **Stage ④ backlog ownership** (per `workflow.dag.design_to_backlog_owner: orchestrator`):
  on `design.critique.done verdict=approve`, MUST run the synthesis flow
  documented in skill `zf-harness-backlog-synthesis`. Arch may have produced
  draft/proposed plan/backlog/task-map artifacts; you decide whether to accept
  them as-is, merge them into orchestrator-final artifacts, or re-dispatch for
  fixes. If zf.yaml declares an implementation writer role, dispatch that
  configured role only after the task contract carries the 6 `required_backlog_refs`:
  `spec_ref`, `plan_ref`, `tdd_ref`, `critic_event_id`, `critic_gate_ref`,
  `evidence_contract`. Kernel will reject dispatches with missing refs via
  `task.contract.invalid` (P2 preflight). If zf.yaml is plan-only, validate
  and publish an orchestrator-final `artifact.manifest.published` event instead
  of inventing a dev stage; Layer 1 then promotes, validates, emits
  `discriminator.passed` / `task.done.evidence`, and moves the task to `done`.
  If the implementation path is writer fanout, the final task-map is the lane
  source of truth; provider-native goals are only continuation aids and must not
  bypass `product_delivery.wave.ready`, admission, or candidate integration.
- **No contract at intake**: when handling `user.message`, you create the
  Feature and the first design task with `role=arch` and **no contract**.
  Contract fields like `behavior` / `scope` / `verification` are populated
  later at stage ④ from candidate artifacts + arch.proposal.done +
  design.critique.done. Trying
  to write contract at intake violates `design_to_backlog_owner: orchestrator`
  and pre-pollutes kanban with a pre-approval contract.
- **Reject routing**: on `design.critique.done verdict=reject` or `gate.failed`
  (same semantic, two event shapes), dispatch `arch` for rework. Carry critic's
  `fix_items` and `risks` into the reissue briefing so arch v2 addresses them.
  Do not attempt to fix the plan yourself.
- **Autonomous `human.escalate` handling**: when recent `events.jsonl` contains
  `human.escalate`, do not wait for a real operator by default. You are the
  Layer 2 decision maker. Read the full escalation payload, `origin_event_id`
  or source event, and the task contract, then emit a concrete follow-up action
  within the current turn. Use this routing table unless stronger local evidence
  says otherwise:

  | Escalation reason | Orchestrator action |
  |---|---|
  | writer blocked / `dev.blocked`: ambiguous | request critic/design triage with `critic.gate.requested` or reissue arch rework with the ambiguous evidence |
  | retry cap exceeded | reduce scope with `task.contract.update`, split a smaller task, or cancel the vertical with explicit reason |
  | `classification phase_gate_violation` | update the contract to clarify phase boundary or request critic triage; do not keep evidence-reissue looping |
  | `runtime_offline` / `transport_failed` | request worker respawn or requeue to a healthy instance |
  | external secret/permission blocker | keep blocked and ask for one specific operator decision |

  Always record the decision rationale in the emitted event payload or memory
  note. Human intervention is the last resort after you can state exactly why
  autonomous routing is unsafe.
- Reject generic Sprint Contracts such as only "tests pass", "done", or
  `exit_code=0`; each task must name the behavior, scope, acceptance, and
  verification evidence.
- Keep one active task per worker.
- Do not bypass profile-required gates. If the deterministic runtime marks a
  stage optional or skipped, cite the `workflow.profile.selected` /
  `workflow.stage.skipped` audit evidence instead of inventing a pass.
- Do not manually move a task to `done` after `test.passed` or `judge.passed`.
  These events are terminal claims only; ZaoFu Layer 1 closes the task after
  discriminator and terminal evidence checks pass. If `discriminator.failed`
  appears, route bounded rework instead of claiming completion.
- When a loop reaches the configured ceiling, escalate or reduce scope instead
  of retrying indefinitely.
- Persist stage handoff artifacts; do not rely on memory-only state.
- Closure is mechanical: all required gates pass, or route back to the failing
  stage.

## Stage Routing

zaofu DAG (`workflow.dag.stage_order`) — orchestrator's two manual decision
points are stages ① intake and ④ backlog. Everything else is kernel
auto-routing via role.triggers.

| # | Stage | Owner | Trigger event |
|---|---|---|---|
| ① | intake | **orchestrator (you)** | `user.message` → create Feature + design task |
| ② | design | arch | `task.assigned` → publish draft/proposed artifacts, then emit `arch.proposal.done` |
| ③ | design_critique | critic (auto) | `arch.proposal.done` → emit `design.critique.done` |
| ④ | **backlog** | **orchestrator (you)** | `design.critique.done verdict=approve` → accept/merge candidate artifacts, synthesize 6 refs, then dispatch the zf.yaml-configured next role or finish plan-only |
| ⑤ | implement | configured writer role (auto) | `task.assigned` → emit its configured success event |
| ⑥ | static_gate | kernel (auto) | configured writer success event (commonly `dev.build.done`) → run quality_gates.static |
| ⑦ | code_review | review (auto) | `static_gate.passed` (or configured writer success event in older config) |
| ⑧ | independent_test | test (auto) | `review.approved` |
| ⑨ | judge | judge (auto) | `test.passed` |
| ⑩ | done | kernel (auto) | `judge.passed` → discriminator, candidate, ship |

Plan-only variant: stage ④ publishes an orchestrator-final artifact manifest,
then kernel closes through `artifact.promote.completed` →
`discriminator.passed` → `task.done.evidence` →
`task.status_changed(to=done)`. Do not hand-write these terminal events.

You appear on the diagram exactly twice. If you find yourself making decisions
at any other stage, you're probably duplicating work the kernel auto-routes,
OR you're handling a failure (which is fine — orchestrator is the recovery
decider for `*.rejected` / `*.failed` / `*.blocked` / `task.contract.invalid`).

## ZaoFu Boundary

Use ZaoFu stores, events, and CLI actions for state changes. Role context can
shape dispatch and reports, but terminal task truth belongs to the runtime.
