---
name: zf-harness-self-improve
description: "ZaoFu project-level harness self-improvement workflow for Claude or Codex. Use when asked to review recent ZaoFu work, session history, runtime events, manual operations, backlogs, skills, docs, or repeated agent workflows to identify improvements that should become project skills, commands, hooks, cron jobs, Supervisor checks, Autoresearch scenarios, docs, or backlog items. Requires evidence-first read-only discovery before any asset creation."
---

# ZaoFu Harness Self-Improve

## Objective

Find repeatable, costly, error-prone, or context-heavy workflows in ZaoFu
development and turn them into explicit harness assets. This skill is
provider-neutral: use the same process in Claude and Codex.

Default repository-facing output is Chinese unless the user asks otherwise.

## Ground Rules

- Read `AGENTS.md` first and follow ZaoFu repository rules.
- Treat `zf.yaml` as the only control plane. Resolve `project.state_dir`
  instead of assuming `.zf`.
- Do not write runtime truth directly: `events.jsonl`, `kanban.json`,
  `feature_list.json`, `session.yaml`, and `role_sessions.yaml` are kernel
  managed.
- Phase 1 is read-only. Do not create skills, commands, hooks, cron jobs,
  docs, or backlogs until the user approves candidates.
- Hooks, cron jobs, daemon behavior, provider settings, and workflow changes
  require separate explicit approval even if the user says "approve all".
- Prefer backlog/task proposals over direct control-plane changes. Unapproved
  work goes under `backlogs/`; active approved work can move to `tasks/`.
- When creating provider-facing skills, keep `skills/`, `.codex/skills/`,
  and `.claude/skills/` bodies synchronized unless the user asks for a
  provider-specific variant.

## Evidence Sources

Build conclusions from evidence, not intuition. Inspect only what is relevant
to the user's question.

| Source | Use |
|---|---|
| `AGENTS.md`, `.claude/rules/` | repo discipline and backlog/doc rules |
| `zf.yaml` | roles, skills, workflow, state dir, triggers |
| `<state_dir>/events.jsonl` | repeated failures, stuck, rework, manual intervention |
| `<state_dir>/kanban.json` and task traces | task lifecycle and contract drift |
| `<state_dir>/instructions/` | worker briefing friction and missing context |
| `<state_dir>/projections/` | Supervisor, automation, metrics, plan integrity findings |
| `skills/`, `.codex/skills/`, `.claude/skills/` | existing reusable assets and gaps |
| `docs/manual/`, `docs/design/`, `docs/impl/` | documented workflows and outdated guidance |
| `backlogs/`, `tasks/`, `ideas/` | known debt and candidate improvements |
| `git log`, `git diff --stat`, `git status --short` | repeated edit patterns and recent drift |

Provider-local histories such as `~/.claude/projects` or Codex logs may be
useful evidence, but they are not ZaoFu truth.

## Phase 1: Read-Only Discovery

1. Identify a bounded time window or scope. If the user does not specify one,
   use recent repo/runtime evidence plus the current dirty state.
2. Look for repeat signals:
   - the user repeatedly asks for the same review, repair, or manual command;
   - workers hit the same stuck/rework/dispatch/contract failure;
   - a skill or manual exists but agents still redo the process manually;
   - task evidence is scattered across events instead of durable artifacts;
   - a workflow requires fragile copy/paste or provider-specific knowledge;
   - Supervisor or Autoresearch repeatedly flags the same class of issue.
3. Review existing asset descriptions and trigger text before declaring a gap.
   If an existing asset should have handled the workflow, prefer improving its
   name, description, scope, or usage instructions over creating a parallel one.
4. Classify each candidate by asset form.
5. Produce a shortlist and stop for approval.

Shortlist format:

```text
| Rank | Workflow | Evidence | Frequency | Harness pain | Recommended asset | Expected metric improvement | Risk | Approval ask |
|---|---|---|---|---|---|---|---|---|
```

Use concrete evidence references: event ids, task ids, file paths, commits,
manual docs, or command outputs. Do not fabricate counts.

## High-Confidence Criteria

A candidate is high-confidence only when most of these are true:

- occurred at least 3 times across distinct sessions, tasks, or runtime traces;
- each occurrence cost roughly 5+ minutes of manual effort, context loading, or
  recovery work;
- inputs, procedure, outputs, and stopping condition are stable enough to
  package;
- packaging would improve speed, quality, consistency, reliability, or
  observability;
- existing skills, docs, commands, Supervisor checks, or Autoresearch scenarios
  do not already cover it adequately.

If evidence is promising but weak, list the item under "needs more evidence"
instead of creating an asset.

## Privacy

- Do not quote secrets, tokens, API keys, private customer data, private chat
  content, or full provider transcripts into shortlist items or new skill files.
- Reference sensitive evidence by path, date, task id, or event id instead of
  pasting private content.
- If a workflow only makes sense with sensitive data inline, skip packaging it
  or propose a redacted deterministic helper.

## Asset Decision Matrix

| Candidate type | Choose when | Default output |
|---|---|---|
| Skill | Agent needs repeatable procedural knowledge or role discipline | `skills/<name>/SKILL.md` plus provider copies if directly invoked |
| Backlog | Work needs implementation, tests, or design review | `backlogs/YYYY-MM-DD-HHMM-<slug>.md` with `> 状态: proposed` |
| Manual doc | Humans need an operating guide | `docs/manual/` and index update, if docs are tracked for this scope |
| Design doc | Architecture or external control-plane behavior changes | `docs/design/NN-<slug>.md` and `docs/design/00-index.md` |
| CLI command | Repeated deterministic operation belongs in ZaoFu | backlog first, then implementation after approval |
| Supervisor check | Lightweight online attention/inspection | backlog first; ensure runtime caller wiring |
| Autoresearch scenario | Heavy reproduction, repair, validation, reflection | autoresearch backlog or scenario design |
| Hook/cron | Periodic or provider-side automation | separate explicit approval required |
| Extend existing asset | Similar skill/doc/command already exists | patch existing owner instead of duplicating |
| Skip | Rare, low impact, unsafe to automate, or poorly evidenced | record rationale only if useful |

## Phase 2: Approved Implementation

After the user approves specific candidates:

1. Restate the approved candidate ids and the intended files.
2. Re-check `git status --short` and avoid overwriting unrelated user changes.
3. For new skills, use `skill-creator` and keep the body concise.
4. For ZaoFu project skills:
   - create/update `skills/<skill-name>/SKILL.md` for runtime skill sources;
   - create/update `.codex/skills/<skill-name>/SKILL.md` when Codex should
     discover it directly;
   - create/update `.claude/skills/<skill-name>/SKILL.md` when Claude should
     discover it directly;
   - keep duplicate `SKILL.md` files synchronized unless deliberately split.
5. For backlog output, follow ZaoFu backlog rules: UTC filename, first
   paragraph with `> 状态: proposed`, concrete acceptance criteria with
   `step -> verify: check`, and source evidence references.
6. For each newly created skill or command, include a short "how to test" hint
   in the skill body or final report so a future operator can verify trigger
   behavior.
7. Validate any created skill with `quick_validate.py` where available.
8. Report exact files changed and any validation that could not be run.

Do not mark work `done`, emit terminal events, or commit unless the user asks.

## Improvement Metrics

Tie recommendations to measurable harness outcomes:

- lower repeated manual command count;
- lower stuck/rework/dispatch failure rate;
- fewer `task.contract.invalid` or missing evidence failures;
- fewer plan/task/evidence drift findings;
- shorter onboarding/recovery briefing;
- better skill trigger precision across Claude and Codex;
- more durable artifacts instead of event-log archaeology.

## Boundary With Other ZaoFu Loops

| Loop | Purpose | This skill's role |
|---|---|---|
| Supervisor Inspection | Lightweight online detection of attention-worthy drift | Consume findings and suggest assets/backlogs |
| Autoresearch | Heavy reproduce-fix-verify-reflect loop for ZaoFu bugs | Propose scenarios or repair backlogs, not run campaigns by default |
| Project Spine Review | Product delivery alignment | Detect repeated process gaps that need reusable assets |
| Harness Self-Improve | Offline productization of repeated harness workflows | This skill |

## Output Discipline

Phase 1 response should lead with the shortlist and approval asks. Phase 2
response should lead with changed files and validation. Keep prose concise and
grounded in evidence.

How to test: ask "使用 zf-harness-self-improve 审计最近 ZaoFu workflow, 给出可产品化候选。"
