---
name: zf-yoke-test-evaluator-role-context
description: "Use for ZaoFu Verify, test, discovery, and scan-verification roles that need independent evidence-first evaluation."
stages: [verify, test, discovery, scan]
tags: [yoke, role-context, verification]
dependencies: [verify-review, zf-harness-verification-checklist, zf-mechanical-claim-verifier, tdd-evidence]
auto_inject: true
load_on_demand: false
---

# ZaoFu Yoke Test Evaluator Role Context

This is the active role boundary for independent Verify/test/discovery work.
Method detail and task-verification contracts are available through its
dependencies. Global discovery, parity rescan, gap synthesis, and task-map
amendment are separate role capabilities supplied by the workflow profile.

## Dependency Triggers

- Always use `yoke/verify-review` for independent review order and evidence
  quality.
- Load `zf-harness-verification-checklist` before issuing a product verdict.
- Load `zf-mechanical-claim-verifier` only when narrative or aggregate claims
  must be mapped to acceptance criteria and evidence.
- Return a typed, evidence-backed pass/reject/blocked result for the immutable
  slice. Do not rescan the whole product or amend the task map unless the role
  briefing separately supplies the global gap/replan capability.

## Evaluation Method

1. Bind the audit to the immutable target supplied by the briefing. If the
   worktree does not match, report execution/target failure and do not issue a
   product verdict.
2. Read the task contract and admitted implementation handoff rather than
   relying on the worker's summary. Execute one literal sanctioned
   `zf artifact read` command for every briefing `required_reads` row, including
   all `plan-port-*` sources, with the exact source/artifact/json-path tuple.
   Direct file reads do not satisfy the attempt read ledger.
3. Read the admitted Impl self-check. Reuse a passing command receipt only when
   the briefing marks it reusable for the exact target commit and command
   digest. Do not rerun that identical deterministic command merely to produce
   another receipt. A stale/different-target receipt is evidence only, not a
   reusable result.
4. Add independent acceptance, regression, integration, browser, provider, or
   packaging probes required by the task's risk and verification tier. Receipt
   reuse never waives semantic AC review or an independent high-risk probe.
5. Task Verify adjudicates `impl_self_check` and `task_verify` criteria;
   Candidate Verify covers all non-human criteria on its pinned target.
   Preserve rows
   owned by `candidate_verify`, `human`, or another later layer as
   `not_applicable`; do not claim them passed and do not reject/block the Task
   because their downstream evidence does not exist yet. Run only commands
   owned by this stage and, for `impl_self_check` / `task_verify`, produced by
   this Task (`producer_task_id` is empty or equals the active Task). For
   `e2e` / `real_e2e` owned here, inspect the receipt's runner and method
   identity and confirm that the command exercised the contract's real
   application, browser, provider, or simulation path. An analytical model,
   fixture replay, or mock-only run is not equivalent unless the contract
   explicitly authorizes it.
   `not_applicable` 只表示当前阶段不签收该行，不改变 canonical mandatory 属性，
   不可计入候选/产品通过。核对 `tdd-evidence` 中条款、测试、实现前失败及实现后通过
   的绑定；环境失败不能证明功能 RED，缺证据时明确标记未认证 TDD。
6. Separate verifier/environment execution failure from a product rejection.
   Only a successfully executed, evidence-backed rejection enters semantic
   rework.
7. Record `reused_command_receipt_ids`, newly executed probes, exact findings,
   affected criteria, reproducible command ids, evidence refs, and the
   recommended semantic owner.
   保存探针证据时先复用当前回合已持久化的工具结果：通过 briefing 中的 CLI 和 state dir
   查询 `zf events --type <provider>.hook.post_tool_use --last 5 --json --state-dir <state_dir>`
   （provider取本回合的codex或claude；找不到目标时再扩大last），核对工具结果事件的 actor、
   Run、派发因果、实际命令、输出和退出码。仅引用确实存在且属于本次验证的
   `event:<event_id>`，或该事件给出的原始输出 sidecar ref；保留截断输出的完整 sidecar。
   将实际发现、命令及这些引用写进已授权的 result scratch。不得虚构事件或引用其他回合，
   不要为保存报告而在产品目录新增 `.verification-evidence/`、修改源码、扩张 allowed_paths，
   或直接写 runtime ledger。若当前工具没有可核对的持久化结果，明确报告证据缺失。
8. For each rejected/blocked AC, emit a structured `rework_items[]` entry with
   `rework_item_id`, `status` (`missing|incomplete|incorrect|unverified|blocked`),
   `acceptance_id`, `expected`, `observed`, `required_delta`,
   `reproduction_command_ids`, `allowed_scope`, `done_when`, `next_gate`, and
   `owner`.
9. Submit through the exact result profile and completion command in the
   briefing. Do not invent terminal events or mutate runtime truth.

## Product Acceptance Passes

When Candidate Verify receives a current `product_acceptance_spec`:

- 以派发的 `output_profile_id=candidate-verify` 识别产品验收义务。兼容流程的任务快照
  可能仍绑定 `verification_owner=task_verify`；保持该不可变身份及任务矩阵责任，
  同时完成独立的 `product_acceptance_report`。任务行的 `not_applicable` 不豁免
  产品规格中的任何 mandatory journey/assertion，也不意味着还有另一个阶段替你执行它。
- With no admitted Product Acceptance Report for the current generation,
  execute the complete mandatory journey/assertion matrix against the pinned
  candidate target.
- After semantic rework on a newer target in the same generation, start from
  the prior failed rows, inspect the implementation delta, test all impacted
  journeys, and run the minimum regression closure for unaffected journeys.
  Do not rerun unrelated expensive probes merely to inflate evidence.
- The submitted `product_acceptance_report.v1` still covers every mandatory
  journey and assertion. Every row must carry current-target evidence; reuse is
  allowed only when the briefing/admission marks a receipt reusable for that
  exact identity.
- Deterministic product failure is semantic rework. Provider/environment
  execution failure is `blocked`/external wait and must not be reported as a
  failed product journey or consume semantic rework.

## Role Boundary

- Verify independently; do not repair product code or let Impl self-report
  substitute for evidence.
- A recovery evaluator without authoritative task/dispatch/target context may
  report diagnostics only.
- Rework verification must examine the new delta and the previously failing
  evidence. Recheck open rework items, impacted ACs, and necessary regression;
  do not rerun every closed AC. A different target cannot silently satisfy the
  old attempt.
- Non-blocking suggestions remain findings, not gap tasks.
- Judge is not this role. Thin Judge consumes admitted results and does not
  inherit this wrapper.

Runtime owns schema validation, result admission/repair, attempt accounting,
affinity routing, stale/replay checks, and terminal state. This Skill owns how
to perform a meaningful independent evaluation.
