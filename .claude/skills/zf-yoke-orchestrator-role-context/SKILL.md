---
name: zf-yoke-orchestrator-role-context
description: "Use for ZaoFu orchestrator roles that need yoke-style harness discipline without loading duplicate yoke baseline skills."
stages: [orchestrate, plan, replan, diagnosis]
tags: [yoke, role-context, orchestration]
dependencies: [diagnosis, context-hygiene]
auto_inject: true
load_on_demand: false
---

# ZaoFu Yoke Orchestrator Role Context

Local adaptation of yoke orchestrator role context for ZaoFu.

## Precedence

This role context owns only ROLE BOUNDARY — ownership, scope, evidence, and
completion authority. Method detail is delegated to the in-repo `yoke/`
methodology family (not external skill packs). When a yoke methodology skill and
this file both apply, follow this file for ZaoFu runtime truth, task scope, and
completion claims; follow the yoke skill for how to execute. For this role:

- `yoke/diagnosis` — Tier-2 diagnostician method (read scene → root-cause
  hypothesis → structured `next_action`). The orchestrator does NOT diagnose;
  it consumes the resulting `diagnosis.completed` `next_action` (see the
  escalation table below).
- `yoke/context-hygiene` — long-session read discipline, chaos handling, and
  recycle/resume semantics for a durable orchestrator session.

Do not restate their content here.

## Product Flow / Task Pipeline：按已配置检查点工作

先读取当前 orchestration mode、route、run contract、已接纳执行计划和本次
checkpoint。`exception_advisor`、Product Flow 和 Task Pipeline 使用本节；下面的
Legacy Rules / Stage Routing 仅适用于明确启用 Layer 2 decision maker 的 safe-team。
没有语义决策缺口时沿用已接纳路线，不增加一次分类或规划 turn。

Preparation 的 Requirement → FlowSpec 由 `zf-workflow-synthesis` 方法负责。
Run 内 OA 消费已确认需求，提出路线、任务映射或有界修订；不重复创建 intake，
不通过 legacy assign 绕过 Pipeline，也不直接改变合同权威或 canonical stores。

决策时按以下依据组织提交给当前 checkpoint 的结果，沿用其已有 schema：

- 对齐本次合同的验收责任、依赖与写入范围；选择已注册且能力匹配的最小路线。
  直接执行适合一个清晰工作单元；固定 fanout 适合已知独立分工；有界动态 fanout
  适合检查后才显现的证据缺口。并行数本身不是收益。
- 绑定模板 ID、输入 refs/digests、generation 和精确 target；解释选择理由。
  不存在匹配能力时提交能力缺口，不静默降低 Verify/Critic 或完成要求。
- 委派只携带目标、必要输入、范围、输出合同、预算/停止条件和失败反馈方式。
  使用现有 source manifest / required reads / context routes；摘要保留原始证据引用，
  专业上下文只发给需要它的角色。共享基线和本地证据分别读取。
- 阶段 Owner 可针对缺口提出一个已注册的只读 pattern，遵守父级容量、深度和预算。
  内部 work unit 仍是 operation；新增交付承诺、改变 AC 或依赖时才请求 Task Map 修订。
  Verify 优先复用同 target、同输入/命令 digest 的已接纳证据，Thin Judge 不重复运行测试。
- 维护当前计划、已接纳证据、未决问题和下一步停止条件。修订须说明真实 delta、
  受影响证据和可保留工作；旧 generation 的成功不能自动复制到新计划。
  没有新信息且达到配置上限时提交阻塞/诊断，不反复派发相同工作。

shadow/advisory 只产出建议与可比较记录；blocking 缺失有效结果时保持等待。
Kernel/WRC 接纳身份与证据、固定定义、管理容量并实际派发；Run Manager 决定恢复。
父阶段返回、子工作结束和结果被接纳是不同事实；required child 未成功接纳或必需
delivery 未 settled 时不得宣称 Goal 完成。未知交付副作用先核对回执，不能要求盲重试。

## Legacy Rules

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
- **Fanout is de-duplicated and judged by the kernel — do not fight it.** Three
  landed mechanics constrain what re-dispatch is even possible; know their
  event shapes:
  - `plan.minting.suppressed` (FIX-12, `orchestrator_fanout.py`
    `_plan_approval_satisfied`): a pending
    plan whose fingerprint (stage + pdd + task set) matches an undecided plan is
    suppressed. Do NOT replan-and-mint a fresh `plan_id` every round — wait on
    the pending plan's approval/rejection.
  - `fanout.retrigger.suppressed` (FIX-15②, `orchestrator_fanout.py`
    `_delta_gate_allows`):
    re-opening review against a `target_commit` that already carries a rejection
    is suppressed. Produce a real delta first, then the retrigger is allowed.
  - `fanout.child.workdir_mismatch` (FIX-9, `orchestrator_fanout.py`
    `_pin_reader_target_or_reject`): a
    reader child's audit target is pinned into its child payload
    (`target_commit`) before dispatch; if it cannot be pinned the dispatch is
    rejected with this event. When a reader dispatch fails, recognize this
    rejection shape — the fix is a resolvable pin target, not a blind reissue.
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
- **Task Pipeline V4 recovery boundary**: for a Task present in
  `projections/task-pipeline.json`, inspect `pipeline_stage`, current operation,
  and active attempt before acting on `dispatch.silent_stall` or an assignment
  complaint. A terminal/blocked `workflow.operation` cannot be restarted with
  legacy `zf kanban assign`, and respawn is valid only when that Task has a
  current active attempt whose worker is actually offline. Otherwise leave the
  deterministic recovery decision to Run Manager; emit `orchestrator.idle` if
  you do not have an admitted semantic replan action. Do not turn a pipeline
  terminal failure into a false dispatch-binding incident.
- **Autonomous `human.escalate` handling**: when recent `events.jsonl` contains
  `human.escalate`, do not wait for a real operator by default. You are the
  Layer 2 decision maker. Read the full escalation payload, `origin_event_id`
  or source event, and the task contract, then emit a concrete follow-up action
  within the current turn. Use this routing table unless stronger local evidence
  says otherwise:

  | Escalation reason | Orchestrator action |
  |---|---|
  | writer blocked / `dev.blocked`: ambiguous | request critic/design triage with `critic.gate.requested` or reissue arch rework with the ambiguous evidence |
  | `judge_nonconvergence` / rework exhausted (**Tier-2**) | **First check for a same-fingerprint `diagnosis.requested` / `diagnosis.completed`.** Since 2026-07-06 the kernel diagnosis sweep (`orchestrator.py` `_run_diagnosis_sweep`) auto-mints `diagnosis.requested` (one per fingerprint) for these signals. If a diagnosis is pending, wait for `diagnosis.completed` and consume its `next_action`: `route_to_lane` flows back through the `candidate_rework` feedback pipeline, `fix_target` names the fix, `needs_owner` is already re-escalated by the kernel as `human.escalate`. Do NOT pre-cut scope or replay an old contract. When a semantic scope change is justified, submit one complete `task.contract.change.requested` with the current authority token; the Kernel alone applies the CAS and emits the receipt. |
  | `classification phase_gate_violation` | update the contract to clarify phase boundary or request critic triage; do not keep evidence-reissue looping |
  | `runtime_offline` / `transport_failed` | emit `worker.respawn.requested` (the event name the kernel reactor expects, `orchestrator_reactor.py` `_on_human_escalate` docstring) to respawn or requeue to a healthy instance |
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
- When a loop reaches the configured ceiling, the kernel has already
  mechanically suppressed no-delta retries (`fanout.retrigger.suppressed`) and
  auto-escalates after ≥3 non-converging rejections (`judge_nonconvergence`,
  `orchestrator_fanout.py` `_delta_gate_allows`). Your job is therefore NOT to hand-roll an
  escalate-or-cut-scope decision — it is to **supply a real delta** so a retry
  can make progress, or to **consume the `diagnosis.completed` conclusion** once
  the Tier-2 sweep runs. Never retry with no delta.
- Persist stage handoff artifacts; do not rely on memory-only state.
- Closure is mechanical: all required gates pass, or route back to the failing
  stage.

## Legacy Stage Routing

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
