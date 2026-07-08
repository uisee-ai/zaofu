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
     注意:writer/impl slice 的完工事件可能是 `dev.build.done` 或
     `impl.child.completed`(controller/profile flow 发后者,kernel 视为等价——
     `dispatch_routing_queries.py:53`、`terminal_ledger.py:16`、`topology.py`);
     只认单一 `dev.build.done` 会误判 controller/profile 拓扑不可移植。
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
- **局部/存量项目 refactor**:若目标是导入的既有工程做局部重构,task_map 可声明
  `refactor_contract.assembly_policy=none` 和/或 `workspace_root_owner_required=false`
  (顶层或 `refactor_contract` 内)跳过 workspace-root-owner 启发式
  (`lane_pipeline.py:635,685-698`;根标记 pyproject.toml/setup.py/setup.cfg/
  requirements.txt/uv.lock/poetry.lock/Pipfile)。跳过 root-owner 不放松 task
  schema/path/evidence 校验。审存量项目 refactor 时确认此开关已按目标形态设置。
- workflow stages have explicit completion events and next-stage dispatch
  rules.
- fanout stages have bounded stuck handling and child completion
  reconciliation. 注意:不收敛升级不再原地 stuck-guard——现在按 stall 指纹铸
  `diagnosis.requested`(`src/zf/runtime/diagnosis.py`),该请求需要一个诊断
  执行体消费(见下方 Tier-2 检查项),否则 unattended 跑缺 no-dead-end 一格。
- **Tier-2 no-dead-end**:目标 `zf.yaml` 是否配置 diagnostician 角色 + 一个
  `trigger: diagnosis.requested` 的 reader stage 消费诊断请求。kernel 只负责
  按指纹判重铸 `diagnosis.requested` / 消费 `diagnosis.completed`
  (`known_types.py`、`diagnosis.py`);`diagnostician` 角色名与 trigger stage
  是项目配置约定(无内核校验)。preflight 未接线诊断执行体 = judge/candidate
  不收敛升级会 dead-end 空转。
- **verify/judge reader stage 的 `target_ref`**(bizsim r4 F9,launch-blocking):
  reader stage 必须声明 `target_ref` 且可渲染,否则派发被拒并 emit
  `fanout.child.workdir_mismatch`(`orchestrator_fanout.py:6560-6620`);且知晓
  pin-commit/delta 门语义——同一 `target_commit` 无新 commit 的重审会被
  `fanout.retrigger.suppressed`(`orchestrator_fanout.py:6492`)抑制,避免
  "judge 审基线树"级别的静默失败。
- task refs can accept committed handoffs while still rejecting business dirty
  files.
- Supervisor/Autoresearch trigger policy has enough budget for the intended
  run and declares whether repair mode is `proposal_only` or `bounded_repair`.
- `zf recover workflow` or equivalent dry-run can report pending resume
  actions from events。长程 goal loop 的续跑锚点是 `run.goal.started/updated/
  completed/blocked` 与 `run.goal.quiescent.entered/exited`
  (`run_manager.py:112-120`、`quiescent.py:21-22`);任务级续跑走 attempt spine
  `task.attempt.started/succeeded/failed/heartbeat/retry_scheduled/deadlettered`
  与 `task.rework.continuation_injected`(findings 注入活会话、不重派——
  `lane_micro_loop.py:23`、`candidate_rework.py:241`)。审 unattended 长程续跑
  能力时按这两条 spine 核对。
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
