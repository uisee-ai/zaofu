---
name: zf-cr
description: "Read-only architecture review of the ZaoFu repository before development. Use when Codex / Claude needs to build accurate development context for harness engineering, deterministic kernel/runtime, multi-agent orchestration, state/event invariants, Web/API boundaries, integrations, tests, docs, or refactor planning."
---

# ZaoFu Codebase Review

## Objective

Perform a read-only, architecture-level codebase review that builds accurate
development context before implementation. Focus on ZaoFu's harness
engineering invariants: deterministic kernel, runtime state, event log,
orchestration boundaries, integrations, Web/API projections, tests, and docs.

This skill is the **baseline checklist** for ZaoFu review. It does not claim
"everything is covered" by itself. A complete review must produce evidence:
coverage matrix, file/line references, explicit uncovered areas, and a
follow-up prompt or supplement plan for gaps.

Default to Chinese for repository-facing prose unless the user asks otherwise.

## Modes

- If the user asks to review the repository, execute the review workflow
  below.
- If the user asks for refactor planning, run the adaptive full-review loop:
  bootstrap -> dynamic Review Prompt V2 -> deep review -> coverage audit ->
  synthesis.
- If the user asks for a reusable command, generate a Codex command using
  `codex exec --ephemeral --ignore-user-config -s read-only -a never`.
- If the user explicitly asks for parallel or multi-agent review, split
  the review into independent read-only areas. Otherwise perform the
  review locally.

## Safety Rules

- Read `AGENTS.md` first and follow its repository rules.
- Do not modify files, create files, emit ZaoFu events, or update runtime
  state during review.
- Do not run commands that mutate the repo, configured state dirs,
  `.zf/`, caches, lockfiles, progress files, kanban files, or session
  files.
- Treat `.zf/` and configured state dirs as runtime state, not source
  code.
- Do not hard-code `.zf`; verify behavior against `zf.yaml` and
  `project.state_dir`.
- Prefer `rg`, `rg --files`, `git status --short`, `git diff --stat`, and
  targeted file reads for inspection.

## Review Workflow

### Step 0 — Bootstrap Strategy

Use this skill first to build the review map, not as the final review answer.
The first pass should output:

- repository map: important directories, runtime state location, major
  subsystems
- invariant map: state ownership, event ownership, task contract, Web/API
  mutation boundary, skill/workdir boundary
- hot spots: recent git changes, oversized modules, TODO/defer debt, failing
  or risky areas
- initial coverage matrix: subsystem -> files inspected -> evidence refs ->
  uncovered refs
- dynamic Review Prompt V2: a precise prompt for the second-pass deep review

If the user asks for "full review", "refactor scan", or "plan generation",
do not stop after bootstrap unless explicitly asked.

### Step 1 — Anchor Reading

Read these **4 anchor sources before anything else**. They save most of the
time the reviewer would otherwise spend rediscovering structure:

1. `AGENTS.md` — repository rules at top level
2. `docs/design/00-index.md` — canonical doc map, lists design docs with
   one-line summaries + status
3. `docs/design/44-zaofu-self-assessment-multi-agent-long-horizon.md` —
   recent self-assessment with Multi-Agent / Long-Horizon scoring and
   change-tracking table; treat it as baseline to verify, not truth to copy
4. `.claude/rules/code.md`, `.claude/rules/backlogs.md`,
   `.claude/rules/docs.md` — path-scoped rules and backlog/doc discipline

Then absorb the layout:

- `zf.yaml` (the only control plane)
- `src/zf/cli/`, `src/zf/core/`, `src/zf/runtime/`,
  `src/zf/integrations/`, `src/zf/autoresearch/`, `src/zf/web/`
- `tests/`, `web/`
- `docs/design/` (especially recent docs)
- `skills/` (ZaoFu's deliverable runtime skills)
- `.codex/skills/` and `.claude/skills/` (provider-local skills)

### Step 1.5 — Full Git History Map

Reviewing without the full project history misses architectural drift. Build a
full-history map first, then use recent commits only as hotspot signals.
Always run:

```bash
git log --all --oneline --decorate
git log --reverse --oneline | head -40
git log --all --name-only --pretty=format: | sort | uniq -c | sort -nr | head -80
git shortlog -sn --all
git log --all --since="2 weeks ago" --oneline | head -50
git diff --stat HEAD~30 2>/dev/null | tail -10
```

Use the complete `git log --all` to identify eras, feature families, subsystem
ownership, reversions, and long-running refactor debt. Use the recent scan to
rank likely regression hotspots. Sprint families to recognise: `feat: TR-* /
EVAL-* / PWF-* / ZF-LH-* / ZF-PWF-* / omega-* / alpha-* / beta-*`.

The review output should include a short Git History Map:

- major eras and feature families
- high-churn files/directories
- recent drift hotspots
- stale docs/backlogs that conflict with current code
- commits or commit families that deserve targeted follow-up

### Step 1.6 — Global Architecture Inventory

Before deep review, build a whole-system inventory so the second-pass prompt is
not biased toward only the files already known to the reviewer:

- entrypoints: CLI commands, Web/API routes, runtime loops, background
  sweepers, scripts, examples
- data/control flow: config -> kernel -> events/stores -> runtime projections
  -> Web/API/operator surfaces
- external contracts: provider CLIs, tmux, git worktrees, browser E2E,
  Feishu/OpenClaw integrations, file artifacts
- state/artifact inventory: source files vs runtime truth vs rebuildable
  projections vs exported reports
- security boundaries: tokens, permission modes, path guards, read-only vs
  mutation routes, secret redaction
- compatibility surfaces: examples, runbooks, CLI flags, YAML schema, skill
  source resolution, migration behavior
- observability: events, traces, diagnostics, costs, token/context metrics,
  stuck-worker and recovery signals
- performance/scale risks: oversized handlers, polling, log scans, fanout
  concurrency, Web payload size, context growth

### Step 2 — Control-Plane Invariants

- Confirm `zf.yaml` is the only control-plane config.
- Search for hard-coded `.zf`, direct state path assumptions, duplicate task
  contracts (`sprint_contract` vs `contract`), and schema drift.
- Verify `project.state_dir` is respected by CLI, runtime, Web/API,
  simulations, examples, and tests.
- Check YAML schema/backward compatibility: old examples should fail clearly or
  migrate explicitly, not silently activate stale behavior.

### Step 3 — State Ownership

- Verify kernel-managed runtime truth flows through `EventWriter` /
  `EventLog`, `TaskStore`, `FeatureStore`, and `SessionStore`.
- Flag direct writes to `events.jsonl`, `kanban.json`, `session.yaml`,
  `feature_list.json`, or `role_sessions.yaml` outside allowed helpers.
- Treat skills, workdirs, lockfiles, progress, cost, diagnostics, traces, and
  dashboard state as rebuildable projections unless code proves otherwise.
- Trace event type -> payload schema -> projector/store update -> Web/API
  projection for the critical workflows under review.

### Step 4 — Runtime / Orchestration Boundaries

- Separate deterministic kernel behavior from agent-driven behavior.
- Review orchestrator, worker protocol, Star fanout, child/synth payloads,
  inline override, heartbeat, recovery, stuck-worker fallback, rework routing,
  and task completion gates.
- Flag self-declared completion paths without supporting deterministic gate
  evidence.
- Check the 4-level rework cap (evidence_reissue / respawn / dispatch_retry /
  circuit_breaker) actually bounds long-running failures.
- For Star/refactor-planning workflows, verify reader/writer/synth routes,
  artifact contracts, `target_ref` behavior, and final plan materialization
  boundary.
- Check concurrency semantics: fanout/fanin ordering, idempotency, retry
  dedupe, stale worker cleanup, and whether independent tasks can be traced
  under one parent without false dependencies.

### Step 5 — Integrations + Web/API

- Verify integrations do not write business truth directly or couple to
  orchestrator internals.
- Verify Web/API projections stay read-oriented unless a deterministic,
  token-gated kernel action path exists.
- Check workspace/project switching, Kanban agent, channel/group chat,
  headless provider sessions, token/context telemetry, and runtime metrics for
  project-state leakage.
- Check UX-backed runtime claims: if the UI shows streaming, progress, token
  usage, role context, or completion status, verify the backend source of truth
  and failure state.

### Step 6 — Tests / Docs + Backlog Hygiene

Before judging gaps, run the backlog audit dry-run if available
(`/audit-backlogs` when the local slash command exists, otherwise the manual
recipe in `.claude/rules/backlogs.md`). Its TRUE-DEFER list is the inventory
of known not-done work with explicit trigger conditions. Do not re-discover
what audit already catalogued.

Then:

- Identify coverage gaps for config validation, runtime state transitions,
  event append/query behavior, orchestration, verification, recovery, Web/API,
  Star fanout, skills, workspace/project, and regressions.
- Check whether architecture, runtime, config schema, Web/API, security, or
  external control-plane changes are reflected in relevant docs.
- Check doc 00-index hygiene: new design docs registered? orphan docs?
- Check task/backlog files follow status, acceptance, and done/defer rules.
- Cross-check examples and runbooks against current validation/runtime code;
  stale examples are review findings when they can mislead real runs.

### Step 7 — Module Size Baseline

Known oversized files are explicit defer debt, not surprise findings:

| File | Last known LOC | Status |
|---|---:|---|
| `src/zf/web/server.py` | ~7000+ | explicit defer |
| `src/zf/runtime/orchestrator.py` | ~3500+ | already split, residual cohesion |
| `src/zf/runtime/orchestrator_dispatch.py` | ~3400+ | already split, residual cohesion |

Do not repeat-flag these as new findings. Instead check:

- Has any new file violated the <=500-line discipline?
- Has any new code been appended to the 3 oversized files instead of creating
  a sibling module when a sibling owner would be clearer?
- Did a refactor reduce or increase coupling around these files?

### Step 8 — Coverage Audit

After the deep review, run a coverage audit before synthesis. The audit is
fail-closed: uncovered areas stay explicit and must not be hidden in a
"complete" conclusion.

Minimum coverage matrix:

```json
{
  "subsystem": "runtime/orchestration",
  "expected_paths": ["src/zf/runtime/", "tests/test_star_topology.py"],
  "inspected_paths": ["src/zf/runtime/orchestrator.py"],
  "evidence_refs": ["src/zf/runtime/orchestrator.py:1234"],
  "coverage": "partial",
  "uncovered": ["writer fanout resume route"],
  "followup_prompt": "Review writer fanout resume route..."
}
```

Coverage categories:

- `git_history_drift`
- `control_plane`
- `state_event_truth`
- `runtime_orchestration`
- `star_fanout`
- `skills_materialization`
- `workspace_project`
- `integrations_web_api`
- `kanban_agent_channels`
- `autoresearch_eval_loop`
- `provider_headless_streaming`
- `security_permissions`
- `observability_diagnostics`
- `performance_cost_context`
- `compatibility_examples_runbooks`
- `tests_docs_backlogs`

### Step 9 — Supplement Review

If coverage audit finds meaningful gaps, generate a supplement prompt targeted
only at the missing evidence. Do not rerun broad review unless the repo map was
wrong.

Supplement prompt must include:

- exact uncovered category
- expected paths to inspect
- invariant or behavior to verify
- required evidence shape
- stop condition

### Step 10 — Cangjie-Mono Cross-Check

If `/path/to/project/` exists locally, glance at its recent
`.zf/events.jsonl` tail and `tasks/` for r-next-N failure patterns. ZaoFu's
primary validation loop is cangjie-mono real-task runs; review without this
signal misses where real bugs are.

```bash
ls -t /path/to/project/tasks/ 2>/dev/null | head -5
tail -50 /path/to/project/.zf/events.jsonl 2>/dev/null | grep -E "ship.blocked|judge.failed|discriminator.failed|worker.stuck"
```

## Adaptive Full-Review Loop

Use this loop for refactor-planning or "full review" requests:

```text
bootstrap with zf-cr
  -> repo map + invariant map + hot spots + Review Prompt V2
deep review with Review Prompt V2
  -> findings + refactor slices + verification needs
coverage auditor
  -> coverage_matrix + uncovered + supplement prompts
supplement review if needed
  -> gap-specific findings
synthesis
  -> review.md + refactor-plan.md + task_map/backlogs
```

The final synthesis must distinguish:

- **confirmed**: supported by file/line or command evidence
- **inferred**: likely based on nearby code or docs, but not fully proven
- **uncovered**: not inspected or blocked by time/tooling
- **known defer**: already tracked and not a new finding

## Parallel Review Slices

Use these slices only when the user explicitly requests parallel or
multi-agent review:

- kernel/runtime/state/event invariants
- CLI/orchestrator/worker protocol/recovery
- integrations/Web/API boundaries
- tests/docs/backlog/development risks
- autoresearch / self-eval / metrics
- workspace/project/Kanban agent/channel runtime
- Star fanout / refactor planning / skill materialization
- security / permissions / token-gated mutation routes
- observability / diagnostics / cost and context telemetry
- git-history drift / docs / examples / compatibility

Each slice must stay read-only and report concrete file/line evidence.

## Output Format

Return a concise Chinese report:

1. **Codebase 总览** — 当前系统如何工作。
2. **Git History Map** — 历史阶段、feature family、高 churn 区域、
   近期漂移热点。
3. **核心不变量** — 哪些规则不能破坏。
4. **Coverage Matrix** — category / inspected paths / evidence refs /
   coverage / uncovered。
5. **高风险问题** — 按严重度排序，包含 file:line / 问题 / 失败场景 /
   建议。已知 defer 的超大文件不算新发现。
6. **测试缺口** — 哪些行为最该补测试，与 backlog audit 输出去重。
7. **Refactor Slices** — 可拆分实施单元，包含 allowed_paths、依赖、
   gate、验证命令。
8. **开发建议** — 下一步最值得做的 5-10 个工程任务。
9. **不确定项 / Supplement Prompt** — 说明哪些结论需要进一步验证。

### Full-Green Early Exit

If the repository is genuinely healthy (no high risk, no new rule violations,
coverage matrix complete, and recent docs/tests align), output a compact
version:

1. Codebase 总览
2. 核心不变量确认
3. Coverage Matrix
4. 不确定项 / 持续观察点

Avoid inventing problems. "无高风险" is a valid conclusion only when coverage
evidence supports it.

## Command Template

When asked to produce a command rather than perform the review, use this
template:

```bash
ZAOFU_ROOT="${ZAOFU_ROOT:-$(pwd)}"

codex -C "$ZAOFU_ROOT" \
  -s read-only \
  -a never \
  exec --ephemeral --ignore-user-config - <<'PROMPT'
Use $zf-cr to run the adaptive full-review loop for this repository.
Return repo map, Review Prompt V2, coverage matrix, findings, refactor slices,
and supplement prompts for uncovered areas.
PROMPT
```

Path is derived from `$ZAOFU_ROOT` or the current working directory, not
hard-coded.

## Related ZaoFu Tooling

The review skill works with other tooling already in place:

- `.claude/rules/code.md` — module size + wire-up discipline + changeset
  simplicity
- `.claude/rules/backlogs.md` — sprint/backlog status field vocabulary
- `.claude/rules/docs.md` — doc numbering + 00-index registration
- `.claude/commands/audit-backlogs.md` — optional `/audit-backlogs` slash
  command
- `skills/zf-harness-evidence-collection/` — structured evidence shape
- `skills/zf-harness-instruction-hygiene/` — instruction-source conflict
  handling
- `skills/zf-harness-backlog-synthesis/` — convert findings into candidate
  backlog/task units

If any of these are missing, the review can still proceed but should flag the
absence as a hygiene gap.
