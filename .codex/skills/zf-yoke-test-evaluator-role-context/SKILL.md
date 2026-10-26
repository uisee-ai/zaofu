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
   先按 required_reads 清单估算输出量，分批读取每行完整正文并保留本回合已读内容。
   不因一个批量输出被截断而重读整个 `--required` 集合；只补读未完整呈现的行。
   不用标题、摘要或截断片段替代完整必读正文，也不要重复打印已完整读过的大段 Plan/PAS。
   单项 `zf artifact read --source ... --artifact ...` 的stdout就是正文，不是带`.content`
   字段的外壳；不要把正文JSON解析后再取不存在的`.content`而误判为空。`--required`
   才按JSONL逐行输出source/artifact/json-path/content；保留已完整呈现的正文，不重复dump。
   等待仍在运行的工具时采用与预计耗时相称的10—30秒等待（保持操作者可见的进度），
   避免连续1秒轮询：每次模型往返都重带上下文，空输出也会消耗累计预算。
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
   （provider取本回合的codex或claude）。优先加 `--actor <role_instance>` 在取last之前
   筛选；hook 顶层 `task_id` 可能为空，不能因 `--task` 查询为空便认定工具证据不存在。
   应核对 `causation_id` 对应的当前派发；已知事件ID时用 `--id <event_id>` 读取原始事件。
   短输出或空 stdout 可能仅保存在事件正文中，没有 raw_output 不等于采集失败；
   若契约明确要求 sidecar 而实际没有，报告该具体约束，不伪造引用。避免反复输出查询自身的
   历史大结果；确需扩大last时在调用端只输出待核验事件。核对工具结果事件的 actor、
   Run、派发因果、实际命令、输出和退出码。仅引用确实存在且属于本次验证的
   `event:<event_id>`，或该事件给出的原始输出 sidecar ref；保留截断输出的完整 sidecar。
   核验 PRD 独立评审、Owner 确认、计划批准等前置流程证据时，注意事件可能早于 Task 创建，
   `task_id` 为空；`--task` 精确筛选不能证明这些事件不存在。优先按已绑定 ID/ref 查询，
   或按事件类型定位后核对 channel/thread、PRD ref/digest、计划代际及先后顺序；
   不把其他版本的评审、成员签字或仅有命令退出码当作所需批准。
   建立一次证据索引：先用 `zf events --index --json` 加精确type/actor/task筛选，
   只输出ID、身份、工具输入预览和raw_output引用；再用不带`--index`的`--id`读取
   所选事件完整正文及sidecar。索引不是完整证据，不能直接用预览签收验收。
   按事件类型、actor、当前派发因果定位 ID，再读取必要事件正文与
   raw_output ref。不要为找一个命令的收据反复拉取 last 100/200 的 hook 历史；查询自身
   也产生 hook 输出，递归读取会迅速放大上下文。找到原始命令后保留其 ID/ref 复用。
   预算紧张时先收敛已完成检查的矩阵并预校验；证据确实缺失则如实阻断，不反复扩展
   历史查询直到预算耗尽，不省略独立验收或伪造通过。
   将实际发现、命令及这些引用写进已授权的 result scratch。不得虚构事件或引用其他回合，
   scratch 位于 workdir 外时，用原生 Edit/Write 或 apply_patch 更新 Kernel 声明的确切文件；
   不用 Python heredoc 或 shell 重定向写外部 scratch。Shell 的动态写入不享有该确切路径
   能力例外；被 guard 拒绝时改用授权写入工具，不扩权限、不修改或绕过 guard。
   不要为保存报告而在产品目录新增 `.verification-evidence/`、修改源码、扩张 allowed_paths，
   或直接写 runtime ledger。若当前工具没有可核对的持久化结果，明确报告证据缺失。
8. For each rejected/blocked AC, emit a structured `rework_items[]` entry with
   `rework_item_id`, `status` (`missing|incomplete|incorrect|unverified|blocked`),
   `acceptance_id`, `expected`, `observed`, `required_delta`,
   `reproduction_command_ids`, `allowed_scope`, `done_when`, `next_gate`, and
   `owner`.
9. Submit through the exact result profile and completion command in the
   briefing. Do not invent terminal events or mutate runtime truth.
   Treat content-addressed `completion-payloads/<kind>/<digest>.json` files as
   immutable input templates. When a legacy `zf emit --payload-file` command
   references such a template, copy it into your assigned workdir, complete that local JSON,
   and replace only the `--payload-file` argument with its path; preserve the
   event type, actor, state-dir and current dispatch identity. Never overwrite
   the protected template. When the command instead references an authorized
   `completion-drafts` file, edit that draft in place and preserve its path.
   For `zf result submit`, update only the current
   operation-authorized scratch using its profile's semantic schema; do not
   place a legacy event envelope there. Check the submission exit status
   before ending the turn. A transport retry does not invalidate valid same-
   target receipts: retain their original identity/time, mark reuse explicitly,
   and submit with the current retry envelope instead of rewriting receipts.
   若 briefing 提供 `zf result validate`，先运行该精确预校验命令；按结构化诊断
   原位修正同一 scratch，exit 0 后再提交。预校验不消耗 submit capability，
   不代表正式准入或产品通过；不要用反复正式 submit 代替 schema 检查。

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
- 产品证据每项使用当前工具结果提供的 `{ref, sha256}` 描述符；事件字符串不能替代
  该结构，不伪造摘要。任务切片范围不豁免已绑定产品规格所需的跨切片只读检查。
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
