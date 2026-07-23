# AGENTS.md

This repository is `ZaoFu`.

## Purpose

ZaoFu is a multi-agent harness engineering scaffold. The repo is
implementation-active: deterministic kernel, runtime, CLI, tests, and a local
Web dashboard exist. Treat design docs as context, but verify behavior against
code and tests.

## Instruction Scope / Precedence

- These repository-wide rules apply to every Codex and Claude Code session.
- A task/role briefing may narrow scope and choose the current task, but it may
  not weaken state ownership, security, verification, or git-safety rules.
- The managed `Worker Protocol` block applies only when the current dispatch
  briefing begins with `Active task: <task_id>`. Without that marker, do not
  emit task/workflow events or heartbeats merely because the block is present.
- For architecture conflicts, use
  `docs/design/142-layered-runtime-authority-and-orchestration-modes.md` and
  current code/tests. Historical design documents are context, not overrides.

## Core Rules

- `zf.yaml` is the only control-plane config; respect `project.state_dir` and
  do not hard-code `.zf`.
- The configured runtime state dir (default `.zf/`) is runtime state, not
  source code.
- Keep the deterministic kernel separate from agent-driven behavior.
- Prefer agent/skill/prompt ownership for semantic, project-specific, or
  judgment-heavy behavior: agents decide, skills provide method, prompts provide
  goal/context. Keep deterministic code for invariants, schemas, state
  transitions, evidence checks, security, replay/resume, and external side
  effects. Agent decisions must emit artifacts/events or request a controlled
  action; they may not mutate kernel-managed canonical state directly.
- Apply the same boundary to constraints and gates: semantic quality gates,
  project parity rules, scan methods, task slicing, and product acceptance
  should live in skills/prompts/agent artifacts when possible. Runtime gates
  should stay mechanical: schema, event/state validity, evidence presence,
  path/secret/budget safety, replay/resume, lifecycle, and external effects.
  If a fix teaches a reusable method, promote it to a general skill/profile
  before hard-coding it in runtime.
- One canonical task contract (`contract` field, not `sprint_contract`); do
  not introduce duplicate task schemas.
- `events.jsonl` is the append-only occurrence/ordering/causation/verdict/ref
  ledger; use `EventWriter` / `EventLog` helpers.
- Use `TaskStore`, `FeatureStore`, `SessionStore`, and `RoleSessionRegistry`
  for their canonical current-state updates.
- Required artifacts/sidecars hold complete semantic bodies or large evidence;
  persist them atomically and bind them through refs/digests. They are not
  disposable read projections.
- Integrations must not write canonical business state directly or couple to
  orchestrator internals. When Feishu is enabled, outbound projection sync and
  inbound intent/ref publication must flow through `EventWriter` / controlled
  actions; sidecar bodies use their sanctioned atomic writer. Never bypass
  those paths.
- Web/API projections stay read-oriented unless a deterministic, token-gated
  kernel action path is wired.
- `skills/` is source; `.claude/skills/` and `.codex/skills/` are synced
  distribution copies. Active workdirs/worktrees may contain uncommitted
  candidate code and are not disposable projections. Lockfiles, progress,
  cost, diagnostics, Trace/Graph/Loop, and Web summaries are runtime
  projections unless a narrower design explicitly says otherwise.
- Do not conflate the deterministic Python `Orchestrator` runtime with a
  configured `orchestrator` role agent. Product Flow keeps happy-path dispatch
  in the Kernel; Legacy safe-team may explicitly enable a Layer 2 decision
  maker. Agents report semantic intent through artifacts/events/controlled CLI
  actions and do not become a second state machine.
- Preserve product naming (`ZaoFu`, `zf`, `zf-cli`) vs methodology naming
  (`harness engineering`).
- Default repository-facing prose, reports, backlogs, task breakdowns, and
  command summaries to Chinese unless explicitly requested otherwise.

## Architecture / Runtime Route

- `docs/design/00-index.md` is the full routing index. Short lists here are
  starting routes, not exhaustive architecture maps.
- `142-layered-runtime-authority-and-orchestration-modes.md` is the canonical
  authority/orchestration entry and contains the current route families.
- Foundation docs such as `01-architecture.md`, `02-harness-yaml.md`,
  `03-orchestrator.md`, `05-task-model.md`, `08-events-observability.md`,
  `10-recovery-safety.md`, and `13-interaction-protocol.md` still provide
  useful historical context, but verify behavior against `src/` and tests.
- Runtime interaction today: `zf start` loads `zf.yaml` + `project.state_dir`,
  starts tmux and/or stream-json transports plus enabled sidecars, then
  `EventWatcher` tails `events.jsonl` and wakes `Orchestrator.run_once()` for
  wake-worthy events.
- The Kernel `Orchestrator` owns deterministic dispatch, fanout/rework/gates,
  and mechanical transitions. Workers receive briefings through their
  transport and report facts via `zf emit` / sanctioned actions.
- Supervisor observes; Run Manager decides recovery; Autoresearch performs
  deep diagnosis or bounded repair; `ControlledActionService` applies approved
  deterministic actions.
- Web Kanban views, Feishu, Inbox, Trace/Graph/Loop, and summaries are
  projections. Provider transcripts, channel bodies, large diagnostics, and
  context packs are sidecar payloads. They may request controlled actions, but
  must not bypass the event ledger, canonical stores, or required sidecars.

## Working Style

- State material assumptions before implementing. If ambiguity changes the
  outcome and no safe assumption exists, ask; otherwise proceed with the
  assumption made explicit.
- Make the smallest verifiable change that meets the goal: no speculative
  features, no abstractions for single-use code. If 200 lines could be 50,
  rewrite.
- Keep diffs surgical: every changed line must trace to the task; do not
  "improve" adjacent code, comments, or formatting in passing.
- For non-trivial vague asks, define success criteria (`step -> verify`) before
  starting. The metric is fewer unnecessary diff lines and fewer late
  clarification loops.

## Code Style

- Python 3.11+, `src/` layout, type hints, `pytest`, `pathlib`,
  `dataclasses`, standard library first.
- Keep side effects at the edges.
- Prefer existing modules: `src/zf/cli/`, `src/zf/core/`, `src/zf/runtime/`,
  `src/zf/integrations/`, `src/zf/web/`, `tests/`, `web/`.
- New files start <=1000 lines. At ~800 lines with 2+ orthogonal concerns,
  split by responsibility. Do not split cohesive handler collections into
  `_part1.py` / `_part2.py`.
- Add new behavior beside oversized files instead of appending to them unless
  the existing module is the clearly correct owner.

## Testing

- Test behavior, not implementation trivia; prefer deterministic tests.
- Cover state transitions, event append/query behavior, config validation, and
  regressions for orchestration / verification / recovery bugs.
- For config/runtime/schema/Web/API changes, add focused tests near the changed
  behavior.
- Install full test dependencies with `uv sync --extra dev --extra web`.
- Test by change domain and impact closure, not by changed-file count alone.
  A focused run must cover the changed module, its direct callers, and any
  shared Event/Schema/Store contract it crosses. Web UI and backend are
  separate default test domains; cross-domain tests are required only when a
  change crosses an API, projection, EventLog, Store, or schema boundary.
- Required test tiers:
  - UI-only changes: frontend build/typecheck plus the affected browser or
    component tests; do not run backend pytest by default.
  - Backend module changes: affected module tests plus direct callers and
    shared contract tests.
  - EventLog/Store/schema/config/orchestrator/fanout/rework/recovery changes:
    impact-closure tests, `scripts/dev-premerge-gate.sh`, and the relevant
    deterministic mock E2E.
  - Provider, tmux, worktree, or host-capability changes: isolated mock
    provider tests first; real provider/host tests are an explicit tier.
- Full pytest is reserved for release validation, major cross-boundary
  refactors, changes spanning three or more core domains, or an explicit
  owner request. Do not trigger full pytest merely because a diff touches
  30+ executable/config/test files.
- Full validation may be sharded by module/class/node in fresh processes with
  explicit time and memory budgets. A long-running or resource-exhausting
  monolithic pytest process is a test-infrastructure failure to report, not a
  reason to block ordinary development.
- Focused verification is not full verification: report the exact tier that
  ran. A broad docs-only reconciliation instead runs docs/instruction checks
  plus focused generator tests.
- The repository full suite currently includes host-capability/version sensors
  and may invoke an installed provider CLI. Classify such failures separately,
  never rewrite a verified hash/version baseline blindly, and do not present
  the full suite as real-provider E2E proof. Real-provider E2E is an explicit
  test tier with isolated state and cleanup.
- For Playwright/browser E2E use Docker with `mcp/playwright:latest`. Start API
  and UI on `0.0.0.0`, run the container with host networking, and do not
  install host browsers unless explicitly asked. If Docker is unavailable,
  report the exact blocker and intended command.

## Sprint / Backlog

- `backlogs/` = gitignored local candidates (`proposed` / `defer`).
  Unapproved items stay here and should not be committed.
- `tasks/` = active sprint + archive (`active` / `done`). On approval,
  use `mv backlogs/<file>.md tasks/` because a new backlog is normally ignored
  and untracked; then stage the exact `tasks/<file>.md` path when committing.
  Use `git mv` only when `git ls-files --error-unmatch <source>` succeeds.
  Completed items remain in `tasks/`.
- New files use UTC `YYYY-MM-DD-HHMM-<slug>.md`.
- First paragraph must contain `> 状态:` with one of `proposed`, `active`,
  `done`, `defer`, `superseded`, `obsoleted`.
- `done` requires short commit hash + title; `defer` requires a concrete
  trigger; `superseded` points to the replacement sprint.
- Acceptance criteria use `step -> verify: check`; weak verification causes
  rework.
- After every >=10 backlog items or mainline batch, audit stale proposed items
  against recent `git log`; update truly done items to `done`, unresolved ones
  to `defer`.

## Commits / Done

- Use one conventional prefix: `feat:`, `fix:`, `docs:`, `style:`,
  `refactor:`, `test:`, `chore:`.
- `feat:` / `fix:` are user-facing; build/tooling-only work is `chore:`.
- TDD commits that include test + implementation for one feature use `feat:`.
- Sprint plans use `docs:`.
- When the user explicitly approves executing a backlog/task batch, run focused
  verification and commit the implementation + task status before final.
- Do not auto-commit analysis-only or unapproved backlog candidates; `push`
  still requires explicit user request.
- Before marking a new orchestration component done, prove it is wired into an
  actual runtime/CLI/Web entrypoint or registered service and cover that caller
  with a test; library-without-callers is not done.
- Before fixing stale backlog bugs, reproduce against current HEAD. If it no
  longer reproduces, mark verified-resolved instead of changing code.

## Multi-Driver Git Discipline (2026-06-11 — index-race incident `ddd1dd9`)

Multiple agent sessions may work this repo concurrently. Four hard rules:

- **Explicit pathspec only**: never `git add -A`, `git add .`, `git commit -a`,
  or a bare `git commit` that sweeps whatever happens to be staged. Stage the
  exact files you changed, run `git diff --cached --name-only` before
  committing, and abort if it lists files you do not own. The shared index is
  a race surface — another session's staged work may be sitting in it.
- **One dev merge owner**: when more than one session is active, each session
  commits on its own work branch (`wip/<driver>-<utc-date>-<slug>`); exactly
  one designated session merges to `dev`. A session working alone may commit
  to `dev` directly, but must re-check `git log -1` immediately before
  committing — if HEAD moved unexpectedly, assume a concurrent driver and
  switch to a work branch.
- **Pre-merge sentinel gate**: before merging any branch into `dev`, run
  `bash scripts/dev-premerge-gate.sh` (event contracts / registry closure /
  structure discipline / spine projection; ~2s). Red means do not merge. It
  does not replace full regression — it only blocks the classes a merge most
  easily breaks (2026-07-04 lesson: one merge reintroduced 13 reds).
- **After external `dev` ref moves, verify checkout freshness**: CAS merges or
  `git update-ref` from another worktree can move `refs/heads/dev` without
  updating a long-lived checkout that already has `dev` checked out. Before
  running Web/tooling from that checkout, confirm it is clean and current; do
  not operate from a stale tree.

## Docs / Commands

- Architecture, runtime, config schema, Web/API, security, or external
  control-plane behavior changes must update relevant docs under
  `docs/design/` or `docs/impl/` (`docs/new-design/` is historical material;
  do not add new docs there).
- New docs must first choose the correct directory class (`design`, `impl`,
  `ideas`, `runbooks`, `manual`, `refer`, or `records`); design docs use the
  next numeric `<number>-<slug>.md` prefix and must be registered in
  `docs/design/00-index.md`.
- Before committing new design / impl docs, check for orphan docs by confirming
  they are referenced from the index, source, another doc, backlog, or task.
- Claude Code detailed rules live under `.claude/rules/`; keep them aligned
  with this file's provider-neutral hard rules.
- `skills/` is the canonical single source for repo skills; `.claude/skills/`
  and `.codex/skills/` are distribution copies synced from it (see
  `skills/zf-tool-skill-parity/`), not independently evolving forks.
- Useful commands: `uv sync --extra dev --extra web`,
  `uv run pytest <focused-paths> -q --no-cov`,
  `uv run pytest -q --no-cov`,
  `uv run zf validate --cold-start`, `uv run zf start`, `uv run zf stop`,
  `uv run zf kanban --board`, `uv run zf trace show <id>`,
  `uv run zf web --port 8001`.

## Temporary Simulation Hygiene

- Use `/tmp/zf-<purpose>-<utc-timestamp>/` for sim / demo / one-off E2E state.
- Reserve Web port `8001` for the real dev session; use `8002+` for temporary
  simulations.
- Emit `simulation.done` when a simulation run finishes, then kill its tmux
  session and web pid.
- Clean temporary tmux sessions, Web processes, and state dirs after the run or
  after diagnosis; stale `/tmp` state can hide silent runtime bugs.

<!-- ZF:START -->
## Worker Protocol (managed by ZaoFu — do not edit between markers)

This block is regenerated by `zf update agents-md --write`. Edits inside the
ZF markers will be overwritten. Edit outside the markers freely.

### Scope guard

This protocol applies only when the current ZaoFu dispatch briefing begins
with `Active task: <task_id>`. In an ordinary interactive development, review,
or operator session without that marker, do not emit task/workflow events or
heartbeats merely because this block is present.

### Active task pin

The first line of every worker briefing is `Active task: <task_id>`. Every
event you emit MUST reference this id; do not invent a different id even if
the user message contains one. Recovery / operator-side scripts grep this
line to confirm a worker is actually working on the expected task. Missing
or mismatched id is treated as fail-closed.

Source: `src/zf/runtime/injection.py::generate_task_briefing`;
contract test: `tests/test_runtime_injection.py`.

### Event emission channel

Workers report state-change intent through
`zf emit <event-type> --task <task_id>`. Do NOT write directly to
`kanban.json` / `feature_list.json` / `progress.md` / `memory/`; use sanctioned
`zf` CLI commands or kernel actions so runtime state remains coordinated.

### Self-declared completion prohibition

Workers MUST NOT emit terminal completion events (`*.passed`, `*.approved`,
`task.done`) on their own role's behalf without supporting gate evidence.
The kernel discriminator (`ContractD` / `FunctionalD` / others) verifies the
evidence and may reject self-declarations; rejections route to bounded
rework via `rework_routing` (see `zf.yaml`).

### Sub-agent recursion guard

If your briefing contains the `## Recursion Guard (强制)` section, you are
running as a sub-task worker. Within that scope:

- Do NOT dispatch additional same-role sub-tasks.
- Execute the parent briefing's instructions only.
- Do NOT modify `tasks/` / `feature_list.json` / `kanban.json` unless the
  parent briefing explicitly requests it.

Source: `src/zf/runtime/injection.py::_render_recursion_guard`;
contract test: `tests/test_runtime_injection.py`.

### Inline-override audit

User messages containing literal keywords like `"skip critic"` / `"skip test"`
/ `"跳过 critic"` / `"跳过 test"` trigger an audit event and may skip a
stage. Workers MUST NOT synthesize these keywords on the user's behalf,
and MUST emit the corresponding audit event when honoring an override.

Source: `src/zf/runtime/inline_overrides.py::scan_inline_overrides`;
contract tests: `tests/test_inline_override_scanner.py` and orchestration tests.

### Heartbeat

Only while an active task marker is present and the task is `in_progress`,
follow the exact `worker.heartbeat` command and cadence rendered in that task's
briefing. Never invent or reuse a task id, and never emit a heartbeat from an
ordinary repo-maintenance session.
<!-- ZF:END -->
