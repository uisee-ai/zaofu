---
name: zf-refactor-generalization-audit
description: "ZaoFu refactor portability audit for Claude or Codex. Use before starting or restarting a Hermes/xx-agent/project refactor, or when asked whether recent refactor bugs, fixes, backlogs, or commits are generic enough to reuse in another agent/project refactor. Reviews completed backlogs, commits, tests, zf.yaml, and target project constraints to decide reusable harness capabilities and required preflight adaptations."
---

# ZaoFu Refactor Generalization Audit

## Objective

Decide whether fixes learned from a recent refactor are generic ZaoFu harness
capabilities or one-off project patches, then produce a concrete preflight
checklist for the next target agent/project refactor.

Default repository-facing output is Chinese unless the user asks otherwise.

## Ground Rules

- Read `AGENTS.md` first and follow ZaoFu repository rules.
- Treat `zf.yaml` as the only control-plane config. Respect
  `project.state_dir`; do not assume `.zf`.
- Use committed evidence as the source of truth: `tasks/done`, commit hashes,
  tests, and runtime events. Dirty worktree files are context, not proof.
- Do not mutate runtime truth: `events.jsonl`, `kanban.json`,
  `feature_list.json`, `session.yaml`, and `role_sessions.yaml` are kernel
  managed.
- This skill is read-only unless the user explicitly approves creating or
  updating skills, docs, backlogs, or code.
- If a deterministic checker or runtime change is needed, create a proposed
  backlog first. Do not implement it in the same pass unless approved.

## Inputs

Use only the inputs relevant to the request:

- bounded time window, such as "最近两天";
- target project path and target `zf.yaml`;
- recent `tasks/done/*.md` and related `backlogs/*.md`;
- `git log`, `git show --stat`, and targeted diffs for referenced commits;
- focused tests named in done tasks;
- runtime evidence under the configured state dir when diagnosing a live run.

## Audit Workflow

1. Bound the scope.
   - If the user gives no window, inspect recent commits plus the current
     requested target.
   - Record target repo path, target config path, branch, state dir, and
     backend assumptions.

2. Audit completed backlog evidence.
   - Read only relevant `tasks/done` items.
   - For each item, capture bug class, fix commit, verification command, and
     whether the fix touched harness code or target project code.
   - Prefer done tasks with explicit implementation commit and focused tests.

3. Review the associated commits.
   - Use `git show --stat <commit>` first.
   - Open targeted diffs only when the stat cannot answer ownership or
     genericity.
   - Classify touched files:
     - generic harness: `src/zf/core`, `src/zf/runtime`, `src/zf/cli`,
       `src/zf/autoresearch`, `src/zf/web`, shared tests and docs;
     - config/project adapter: examples, `zf.yaml`, provider setup, skills;
     - project-specific: target application code, one-off branches, manual
       state repair, private provider history.

4. Check portability criteria.
   - Generic when the behavior is config-driven, covered by non-project-only
     tests, and does not hard-code target ids, branch names, paths, or event
     payloads.
   - Needs adaptation when event names, dirty-file policy, provider backend,
     stage names, artifact paths, or LLM validation commands differ by target.
   - Project-specific when it depends on one repo's source layout, manual
     local state, or a non-repeatable prompt/session condition.

5. Inspect the target `zf.yaml`.
   - Verify workflow stages, role backends, event contracts, affinity lanes,
     rework routing, repair policy, trigger budget, state dir, and skills.
   - Confirm scan/plan/artifact/task-map outputs are durable files, not only
     chat text or pane history.
   - Confirm resume/recover paths can infer the next action from events.

6. Produce the decision.
   - `GO`: no P0 gap blocks reuse; config and smoke checks pass.
   - `GO_WITH_ADAPTATION`: reusable harness exists, but target config or
     skills need explicit changes before launch.
   - `STOP`: missing contract, hard-coded assumption, broken smoke, or no
     recovery path for a known critical failure.

## Output Format

Lead with a concise decision, then include this table:

```text
| Bug/fix class | Evidence | Genericity | Target adaptation needed | Next action |
|---|---|---|---|---|
```

Then include:

- target preflight checklist;
- known residual risks;
- proposed backlog/skill/doc assets, if any;
- exact commands already run and commands still recommended.

## Preflight Checklist

Before starting a long refactor on a new target project:

- `zf validate --cold-start` passes for the target config.
- Target `project.state_dir` is isolated and not stale from another run.
- workflow stages have explicit completion events and next-stage dispatch
  rules.
- fanout stages have bounded stuck handling, child completion reconciliation,
  and stale-run guards.
- task refs can accept committed handoffs while still rejecting business dirty
  files.
- Supervisor/Autoresearch trigger policy has enough budget for the intended
  run and declares whether repair mode is `proposal_only` or `bounded_repair`.
- `zf recover workflow` or equivalent dry-run can report pending resume
  actions from events.
- scan, plan, task map, implementation evidence, review, and verify artifacts
  are persisted as files.
- target-specific validation commands are declared, including LLM/function-call
  checks when relevant.
- Web/API projections are read-oriented and show resume or stuck diagnostics
  without writing business truth directly.

## When To Create Follow-Up Assets

- Create a proposed backlog when a repeated check should become deterministic.
- Update this or another skill when the missing piece is agent procedure.
- Update docs/manual only when humans need a stable operating guide.
- Do not add hooks, cron jobs, or daemon behavior without separate explicit
  approval.

## How To Test

Ask: "使用 zf-refactor-generalization-audit 审核最近两天 refactor bug，判断能否复用到新的 xx-agent 重构，并给出 preflight 检查。"
