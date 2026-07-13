---
name: zf-harness-commit-push
description: "Safe ZaoFu commit and push workflow for Claude or Codex. Use only when the user explicitly asks to commit, push, or wrap up approved changes. Inspects git state, separates unrelated dirty files, checks for secrets and risky staging, uses conventional commit messages, stages explicit paths only, never force-pushes, runs the dev pre-merge sentinel gate before merging to dev, respects multi-driver wip-branch discipline, and reports verification. Also carries large/risky diff fallback classification."
---

# ZaoFu Harness Commit Push

> Absorbs zf-diff-size-review-fallback.

## Objective

Commit and push approved ZaoFu changes without sweeping in unrelated work,
leaking secrets, bypassing hooks, colliding with a parallel driver, or violating
repository rules.

Default repository-facing output is Chinese unless the user asks otherwise.

## Relationship to `yoke/git-evidence`

This skill and `yoke/git-evidence` split the git surface; do not duplicate them:

- `yoke/git-evidence` is the **worker-facing reference/evidence model** — which
  refs exist (`task/<TASK-ID>` task_ref, `worker/<instance>`, `candidate/<PDD>`,
  main checkout), who writes/reads each, and why touching a kernel-consumed ref
  forges the evidence ledger. A worker reasons about *what a ref means* there.
- **This skill is the operator/driver commit-and-push procedure** — inspecting
  dirty state, isolating unrelated files, scanning for secrets, choosing a
  conventional prefix, staging explicit pathspecs, running the dev pre-merge
  gate, and pushing. It reasons about *how a human-approved change lands*.

When a decision is about ref semantics or candidate integration, defer to
`yoke/git-evidence`; when it is about staging and landing an approved diff, use
this skill.

## Preconditions

- Use this skill only after an explicit user request to commit and/or push.
- Read `AGENTS.md` and `CLAUDE.md` commit rules before acting (Multi-Driver Git
  Discipline and the dev pre-merge gate are mandatory, not advisory).
- Do not commit analysis-only notes, unapproved backlog candidates, or unrelated
  dirty files.
- Never use `git add -A`, `git add .`, `git commit -a`, `git commit --amend`,
  `git commit --no-verify`, `git push --force`, or `+refs`.
- If the diff is too broad to understand, stop and summarize what needs user
  confirmation before committing (see Large/risky diff fallback below).

## Workflow

1. Inspect:
   - `git status --short --branch`
   - `git diff --stat`
   - `git diff --name-only`
   - `git log --oneline @{u}..HEAD` when an upstream exists
   - `git log -1 HEAD` — record the current HEAD. If you are a solo driver on
     `dev` and HEAD later moved without your commit, assume a parallel driver is
     present and switch to a work branch (see Multi-Driver Git Discipline).
2. Classify changes:
   - include only files touched for the approved request;
   - list unrelated dirty files and leave them unstaged;
   - if only unpushed commits exist and the user asked to push, use push-only.
3. Safety scan:
   - inspect changed filenames for `.env`, key files, credentials, tokens, or
     generated runtime state;
   - inspect added diff lines for obvious API keys, private keys, PATs, access
     tokens, and secrets;
   - treat `docs/` examples carefully, but defang real-looking credentials.
4. Verify:
   - run focused tests or validation appropriate to the changed files;
   - for skill-only changes, validate each changed `SKILL.md` frontmatter with
     the kernel's `read_skill_metadata(path, expected_name=<dir name>)`
     (`src/zf/core/skills/provenance.py`) so `name` matches the directory, and
     run the skill-metadata sentinel `tests/test_skill_provenance.py`;
   - if validation is not run, state the exact reason.
5. Commit:
   - choose one conventional prefix: `feat:`, `fix:`, `docs:`, `style:`,
     `refactor:`, `test:`, or `chore:`;
   - stage explicit paths with `git add -- <path> ...`;
   - before committing, self-check `git diff --cached --name-only` contains no
     other driver's files;
   - commit once with a concise message.
6. Push / merge to `dev`:
   - if landing a work branch into `dev`, first run the **dev pre-merge sentinel
     gate** `bash scripts/dev-premerge-gate.sh`; red means do not merge;
   - run `git push`;
   - if no upstream exists, use `git push -u origin HEAD`;
   - never force-push.

## Multi-Driver Git Discipline

(Codified 2026-06-11 after the `ddd1dd9` index-race incident; see AGENTS.md
§Multi-Driver Git Discipline and CLAUDE.md Commit Conventions.)

- Only explicit-pathspec `git add` is allowed — never `-A` / `.` /
  `git commit -a` / bare `commit`.
- When multiple sessions run in parallel, each driver works on its own
  `wip/<driver>-<utc-date>-<slug>` branch, and exactly one session holds the
  `dev` merge right.
- A solo driver committing straight to `dev` must re-check `git log -1 HEAD`
  first; if HEAD moved unexpectedly, a parallel driver exists — stop and move to
  a `wip/<driver>-...` work branch.
- Conflicting staged changes across drivers are exactly the trigger for the
  `operator_confirm` risk tier below.

## Large/risky diff fallback

Treat a large or risky diff as a harness signal, not automatically as an error.
This section (absorbed from the former `zf-diff-size-review-fallback` skill)
extends the commit flow above and `yoke/verify-review`; it does **not**
auto-split commits or override operator approval.

**Do not** classify runtime truth, generated reports, or large artifacts as
source changes without noting their type. A large diff must trigger stronger
review evidence; if it is too broad to understand, stop and ask for operator
confirmation before committing.

### Diff risk policy (skill-owned thresholds)

Use these default thresholds unless the project has stricter rules. The shape
below is a **skill-owned convention — there is no kernel validator** for it (no
`src/zf` schema consumes it), so treat it as guidance, not a validated envelope:

```json
{
  "thresholds": {
    "changed_files_warn": 12,
    "changed_files_high": 25,
    "changed_lines_warn": 600,
    "changed_lines_high": 1500,
    "sensitive_paths_any": [
      "src/zf/core/**",
      "src/zf/runtime/orchestrator*.py",
      "src/zf/core/config/**",
      "zf.yaml",
      "examples/*.yaml",
      "pyproject.toml",
      "web/package-lock.json"
    ]
  },
  "excluded_or_separate_classes": [
    "runtime_state",
    "reports",
    "screenshots",
    "generated_fixtures",
    "vendored_assets"
  ]
}
```

### Classification rules (four tiers)

- `normal`: below warning thresholds and no sensitive paths.
- `warn`: warning threshold crossed, generated/report paths isolated, or one
  low-risk sensitive path touched.
- `high`: high threshold crossed, multiple ownership surfaces touched, or a
  refactor spans source and tests without a slice plan.
- `operator_confirm`: secret-like files, runtime truth files, a broad
  unreviewable diff, or **conflicting staged changes** across parallel drivers
  (the Multi-Driver Git Discipline surface above).

### Review output (skill-owned shape)

Produce a summary of the assessment. Again a **skill-owned convention with no
kernel validator** — do not present it as a validated `.v1` envelope:

```json
{
  "changed_files": 18,
  "changed_lines": 820,
  "sensitive_paths_touched": ["src/zf/runtime/orchestrator.py"],
  "generated_or_runtime_paths": ["reports/example.md"],
  "risk_level": "normal|warn|high|operator_confirm",
  "recommendations": [
    "split_commit",
    "request_sub_review",
    "run_scope_drift_audit",
    "require_stronger_verification"
  ],
  "required_verification": [
    "focused pytest for changed runtime module",
    "schema/config validation",
    "manual scope classification"
  ],
  "operator_confirmation_required": true
}
```

### Mechanical pairings behind the recommendations

Three consequences make these recommendations more than review hygiene:

1. **`split_commit` has a kernel consumer — commit granularity is not cosmetic.**
   Candidate integration extracts commits idempotently by patch-id via
   `git rev-list --cherry-pick` (`src/zf/runtime/candidates.py` `_task_commits`,
   FIX-10): an incremental base that already contains an equivalent patch is
   excluded so it is not re-applied. A big-ball commit that later conflicts is
   rolled back as a whole package (`yoke/incremental-delivery` writes this
   pairing as a contract). So splitting a large diff into coherent commits is a
   mechanical enabler of clean candidate integration, not just tidiness.
2. **`scripts/dev-premerge-gate.sh` must run before merging to `dev`.** The dev
   pre-merge sentinel gate (established 2026-07-04) runs the `<60s` contract
   sentinels (event contracts / registry closure / structure discipline / spine
   projection); red means do not merge. This is the direct answer to this
   skill's "whether commit may proceed" output when the target is `dev`.
3. **`wip/<driver>` branch discipline is the `operator_confirm` trigger
   surface.** Conflicting staged changes between parallel drivers are why the
   Multi-Driver Git Discipline mandates per-driver work branches and a single
   `dev` merge owner; when you see that conflict, escalate to `operator_confirm`
   rather than staging over another driver.

### Fallback output summary

Return:

- diff risk level and which threshold was crossed;
- paths needing sub-review;
- split or verification recommendation;
- whether commit may proceed under explicit pathspec discipline and (for `dev`)
  a green pre-merge gate.

## Message Selection

Use:

- `feat:` for user-facing capability or TDD feature work;
- `fix:` for user-facing bug fixes;
- `docs:` for design/manual/backlog-only plans;
- `test:` for test-only changes;
- `refactor:` for behavior-preserving code structure;
- `chore:` for build, tooling, generated metadata, or skill maintenance;
- `style:` for formatting-only code changes.

## Report

Final response should include:

- commit SHA and message;
- pushed branch or push blocker (including a red dev pre-merge gate);
- staged paths summary;
- validation run and result;
- unrelated dirty files intentionally left out;
- diff risk level when the fallback applied.

How to test: ask "使用 zf-harness-commit-push 提交并 push 当前已批准的 skill 改动。"
