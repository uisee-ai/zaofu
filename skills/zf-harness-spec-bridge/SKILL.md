---
name: zf-harness-spec-bridge
description: Use when producing OR consuming a spec / plan / ADR markdown that downstream tasks depend on. Covers the four-command bridge that turns markdown into ZaoFu kanban state without leaving prompt-engineering on the critical path. Required for arch / orchestrator / critic / review when STRUCTURE.md §12.3 (or the project's equivalent doc-type contract) applies.
---

# ZF Harness — Spec ↔ Kanban Bridge

Project conventions (see project STRUCTURE.md §12.3 in cangjie-mono, or
the equivalent doc-type section in your project) split tasks into two
classes:

- **code-type**: scope under `packages/**` / `apps/**` / `test/**`. Standard
  TDD path. Evidence = test pass + git commit.
- **doc-type**: scope under `docs/**`, single file or subtree. Spec /
  plan / ADR / runbook. Evidence = file exists + grep checklist + git
  commit. **Still goes through the full DAG and still must `git add +
  commit + push` before emitting `dev.build.done`.**

ZaoFu provides four `zf spec` subcommands to bridge markdown → kanban
without per-round prompt engineering. This bridge is skill-pack agnostic:
`agent-skills` is the default content producer, but another planning/spec skill
pack is valid as long as it writes markdown artifacts that these commands can
validate or merge. Use the commands in this order for the **manual / single-session
spec landing** scenario — an arch or operator hand-drafting a spec/plan/ADR md
and turning it into kanban tasks outside of a running fanout.

## Boundary: fanout plan pipeline vs `zf spec ingest`

These two paths do NOT overlap — pick by whether you are inside a fanout run:

- **Inside a fanout run** (plan → writer fanout), the plan stage's downstream
  tasks are minted from the `task_map.json` machine contract, NOT from
  `zf spec ingest`. That path is owned by **`zf-plan-task-map-contract`**, and
  the kernel already de-dupes re-minting by fingerprint — emitting
  `plan.minting.suppressed` when the same task_map would be minted twice
  (`src/zf/runtime/orchestrator_fanout.py:3042,3086`). Do NOT route fanout plan
  output through `zf spec ingest`; it would bypass the task_map contract and the
  suppression fingerprint.
- **Manual / single-session spec landing** (no fanout, an arch/operator drafting
  a spec md by hand) is exactly what `zf spec ingest` is for. Use this bridge
  here; do not hand-roll kanban.json.

## When to use which subcommand

| Subcommand | When |
|---|---|
| `zf spec prompt <md>` | The md has NO frontmatter. Print a ready-to-paste system + user prompt for ANY LLM (claude / codex / Claude Code subagent / browser chat / etc.). |
| `zf spec merge <md> --frontmatter <json>` | You have the LLM's JSON reply (or hand-written JSON). Inject it as frontmatter into the md. Stdin works via `--frontmatter -`. Code fences are stripped automatically. |
| `zf spec validate <md>` | The md has frontmatter. Verify schema, duplicate task ids, missing acceptance/verification, body-orphan refs. **Required pre-emit gate** before `arch.proposal.done` whenever scope contains a spec/plan/ADR file. Exit non-zero blocks emit. |
| `zf spec ingest <md>` | Frontmatter validated. Create the feature + N kanban tasks deterministically (uuid5-keyed feature_id, idempotent re-ingest). Each task gets `task.created + task.contract.update` events with `source=spec_ingest`. |

## Mandatory workflow for arch / orchestrator

When the scope of a task contains `docs/**` (any markdown that downstream
tasks read), follow this order:

1. **Draft the spec.md with frontmatter** (preferred) OR plain md.
   Frontmatter schema is documented in STRUCTURE.md §12.3.
2. **If frontmatter is missing**:
   ```
   zf spec prompt docs/path.md > /tmp/p.txt
   # paste into any LLM, save reply as /tmp/fm.json
   zf spec merge docs/path.md --frontmatter /tmp/fm.json
   ```
3. **Pre-emit gate** (required):
   ```
   zf spec validate docs/path.md
   ```
   Exit code != 0 → **do NOT emit `arch.proposal.done`**. Instead emit
   `clarification.needed` with the validator's stderr.
4. **git add + commit + push** the md (doc-type tasks still require a
   real commit, see `zf-harness-done-contract`).
5. Emit `arch.proposal.done` with `evidence_refs: ["docs/path.md",
   "git:<hash>", "branch:worker/arch"]`.
6. **Downstream** (operator or follow-up task):
   ```
   zf spec ingest docs/path.md            # creates kanban tasks
   ```

## Mandatory checks for critic / review

When reviewing a task whose scope contains spec/plan/ADR markdown:

- Frontmatter exists and `zf spec validate` exits 0
- `events.jsonl` shows `source=spec_ingest` for any task referenced by
  the spec body (or the operator has not yet run ingest — flag this)
- The md file is in a git commit (doc-type still requires
  `dev.build.done evidence_refs` to include `git:<hash>`)

## Anti-patterns

- ❌ arch writes plain md without frontmatter, never runs `zf spec
  prompt`, leaves VS fanout to be re-derived from md every round
- ❌ arch emits `arch.proposal.done` without running `zf spec validate`
  first — downstream then fails with schema errors at ingest time
- ❌ dev / orchestrator hand-rolls multiple tasks by writing kanban.json
  directly instead of via `zf spec ingest` (loses the
  `task.contract.update source=spec_ingest` event trail)
- ❌ doc-type task skips git commit because "no code changed" — workflow
  treats md commit as evidence; without it `task.ref.rejected` fires

## Files referenced by this skill

- `src/zf/cli/spec.py` — the four subcommands' implementation
- `<project>/docs/STRUCTURE.md §12.3` — frontmatter schema + doc-type
  rules (cangjie-mono provides one; other projects may have an
  equivalent section under a different heading number)
- `<project>/CLAUDE.md` — project-specific overrides that take
  precedence over this skill
