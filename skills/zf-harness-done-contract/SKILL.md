---
name: zf-harness-done-contract
description: "Use before a ZaoFu role claims done; requires concrete evidence and preserves terminal state decisions for the deterministic runtime."
---

# ZaoFu Harness Done Contract

> Absorbs `zf-harness-handoff-resume-snapshot`.

Use this before emitting any completion claim. The skill defines the report
shape; ZaoFu runtime decides whether a task can enter a terminal state.

## Done Evidence

A done report must include:

- task id
- role and instance id
- dispatch id from the current briefing
- changed files or produced artifact paths
- commands run with exit codes
- verification tiers with passing command or artifact evidence
- relevant output summary
- `artifact_refs` and `evidence_refs` when this is terminal gate evidence
- acceptance criteria covered
- known risks, skipped checks, or follow-up work
- next expected role or gate

For fanout writer / affinity lane work, also include:

- `fanout_id`, `stage_id`, `child_id`, and `run_id`
- `lane_id`, `stage_slot`, and `affinity_tag` when present
- `task_map_ref` and `source_index_ref`
- assigned `workdir`, `source_branch`, and `source_commit`
- `files_touched` proven inside `allowed_paths` / `exclusive_files`

For pinned audit / reader children, the kernel writes `target_commit` into the
child payload at dispatch (`src/zf/runtime/orchestrator_fanout.py`). Before
auditing, run `git rev-parse HEAD` and compare it against the briefing/payload
`target_commit`; on mismatch report the workdir mismatch instead of auditing
"roughly the right tree". Echo the audited commit in `evidence_refs` (e.g.
`target-commit:<sha>`) so the claim is bound to the pinned audit object. This
is the same audit-object binding required by yoke/verify-review.

For fanout writer children, before emitting `dev.build.done` or its
controller/profile equivalent `impl.child.completed`, the assigned workdir must
be committed and clean. Verify `git status --short` inside the assigned workdir,
commit the intended changes on the worker branch, and set `source_commit` to
that commit. Do not emit completion with uncommitted changes; the runtime will
reject the task ref and force a repair/rework path.

Writer completion is not spelled only `dev.build.done`. Controller/profile
workflows close a writer task slice with the canonical stage-child event
`impl.child.completed` instead of the legacy `dev.build.done`, and the kernel
treats them as equivalent: `impl.child.completed` is a terminal-success event
(`src/zf/runtime/terminal_ledger.py`), a handoff-success trigger for the
reconciler (`src/zf/core/workflow/topology.py`), and — under worktree mode —
carries the same task-ref-on-completion requirement as `dev.build.done`
(`src/zf/runtime/dispatch_routing_queries.py`,
`src/zf/runtime/orchestrator_reactor.py`). The failed / rework counterpart is
`impl.child.failed` (a `rework_trigger` in `topology.py`), the stage-child
analogue of `dev.failed`. So whichever form your flow emits, the same
done-contract rules in this skill apply — evidence, committed-clean workdir,
in-scope files, and the dispatch id all bind to `impl.child.completed` exactly
as they do to `dev.build.done`.

For rework, also include the delta from the failed attempt:

- trigger event or rework request id
- required rework items addressed
- files, tests, docs, artifacts, or command evidence changed this attempt
- if no code change was required, a concrete no-code rationale and evidence

## Evidence Trace Back to Backlog (6 required_backlog_refs)

When the task contract was synthesized by orchestrator at stage ④ backlog
(per `docs/impl/22-zaofu-canonical-dag.md`), the kanban entry carries 6
upstream pointers: `spec_ref`, `plan_ref`, `tdd_ref`, `critic_event_id`,
`critic_gate_ref`, `evidence_contract`.

A complete done report SHOULD echo back at least two of these in its
`evidence_refs` array, so review / test / judge can audit the implementation
against the original design audit trail without re-discovering it:

Lifecycle completion events use `artifact_refs` as replayable **string paths**.
Structured manifest refs belong in `artifact.manifest.published` or in a
separate `artifact_manifest_refs` diagnostic field emitted by the kernel. Do not
put object refs into role terminal claims.

The kernel hard-requires `summary`, `changed_files` (a list — `[]` is valid
for read-only audits), and a non-empty `evidence_refs` on every completion
event; `verify.passed` / `test.passed` / `judge.passed` additionally require a
non-empty `tests_run`. Missing any of these emits `task.contract.invalid`
(`src/zf/core/events/payload_schemas.py`). `residual_risks` and
`next_agent_input` are warn-only: their absence does not block, but is
surfaced by `zf handoff --score`. A complete payload:

```json
{
  "dispatch_id": "<from briefing>",
  "state": "DONE",
  "summary": "<one-sentence>",
  "changed_files": ["<files this attempt touched; [] if read-only audit>"],
  "tests_run": ["<command + result; required for verify/test/judge events>"],
  "artifact_refs": ["<files you produced>"],
  "evidence_refs": [
    "git:<commit-sha>",
    "branch:worker/<role>",
    "spec:<contract.spec_ref>",                  ← echo backlog.spec_ref
    "critic-audit:<contract.critic_event_id>"    ← echo backlog.critic_event_id
  ],
  "residual_risks": ["<known risks, skipped checks, follow-up work>"],
  "next_agent_input": "<what the next role or gate needs from this handoff>"
}
```

When the contract has `evidence_contract: {"runtime": "<cmd>"}` and your
verification ran that command, include its exit code + output snippet under
that key:

```json
{
  "evidence": {
    "runtime": {"command": "<as declared>", "exit_code": 0, "passed": true}
  }
}
```

This trace-back lets the downstream chain answer: "does the dev's delivery
match what arch promised + critic approved?" without re-parsing 6 events.
If you skipped any required ref because the backlog itself was incomplete,
flag it as a risk so orchestrator's P2 preflight catches the gap.

## Cannot Claim Done

Do not claim done when:

- tests or required checks were not run and no reason is provided
- review/test/judge gates are still pending
- the implementation changed files outside the allowed scope
- writer workdir has uncommitted changes, or `source_commit` does not identify
  the committed handoff
- the lane claim omits required fanout metadata or tries to claim global feature
  / product completion
- evidence exists only as a narrative summary
- declared `verification_tiers` are not covered by passing structured evidence
- the current briefing has a dispatch id but the completion event/report omits it
- this is a rework attempt and no concrete delta from the failed attempt is shown
- there is an unresolved blocker, ambiguity, or failing command

`DONE_WITH_CONCERNS` and `BLOCKED` are report vocabulary only — a skill-owned
convention with no kernel consumer for these strings. Write
`DONE_WITH_CONCERNS` in the report when work is complete but evidence is
partial or a risk remains, and record the concern in `residual_risks`. When
deterministic progress requires another input or runtime action, do not just
write `BLOCKED` in prose: emit the real event channel the kernel reacts to
(e.g. `dev.blocked` with the reason) so the orchestrator can route it.

## Resumable Handoff (absorbed from zf-harness-handoff-resume-snapshot)

A terminal report must let the next role — human or LLM — resume without
replaying chat or scanning `events.jsonl`. That property comes from the flat
top-level payload fields above, **not** from a nested `handoff_summary`
envelope:

- Do not wrap evidence in a `handoff_summary` object. The kernel reads flat
  top-level fields (`evidence_refs`, `artifact_refs`, `risks` /
  `residual_risks`, `dispatch_id`); nesting `evidence_refs` inside an envelope
  makes the top-level evidence check fail
  (`src/zf/core/verification/evidence.py`) and invalidates the claim.
- The State Packet projector derives `completed` / `evidence` from event
  types; it does not read any `handoff_summary` payload field.
- Map handoff intent onto the contract fields: what was done → `summary`;
  proof → `evidence_refs` / `artifact_refs`; open questions and skipped
  checks → "known risks, skipped checks, or follow-up work"
  (`residual_risks`); who picks this up → "next expected role or gate"
  (`next_agent_input`).
- The kernel builds its own `handoff-summary.v1` from your completion event
  (`src/zf/runtime/handoff_summary.py`) and scores it with a
  `handoff-quality.v1` scorecard whose status becomes `needs_handoff_fix`
  when risks, test evidence, next action, or work refs are missing. It is a
  read-only projection surfaced in the `zf web` dashboard's handoff/task view
  (`src/zf/web/projections/summaries.py`), not a blocking gate — and not the
  same thing as the `zf handoff --score` CLI, which runs a separate
  `compute_handoff_score` scorer (`src/zf/cli/handoff.py`). `needs_handoff_fix`
  on your handoff means the fields above were incomplete.

Handoff anti-patterns:

- "done, next role take over" with no `evidence_refs`
- decisions recorded only in commit messages instead of the report payload
- listing open questions while naming no next role or gate to own them
