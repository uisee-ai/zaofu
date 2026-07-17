---
description: Audit backlogs/ and tasks/ for stale "状态: backlog" headers. Cross-reference git log + src/ references. Classify each as DONE-committed / DONE-uncommitted / TRUE-DEFER and produce report. Pass --apply to write DONE updates (DEFER stays untouched).
argument-hint: [--since "2 weeks ago"] [--apply]
allowed-tools: Bash, Grep, Read, Edit
model: sonnet
---

# Audit Backlogs

## Goal

Find `backlogs/` and `tasks/` files where `> 状态: backlog` (or
`proposed`) header is **stale** relative to actual git history,
classify each, and update DONE ones in place. DEFER ones stay
untouched — they need human-supplied trigger conditions.

The exhaustive procedure used to derive this command lives in
`.claude/rules/backlogs.md` § "周期性 audit"; this command is the
mechanical executor.

## Argument parsing

Parse `$ARGUMENTS`:
- `--since DATE` → use this for git log; default `"2 weeks ago"`
- `--apply` → after reporting, actually write status header updates
- Without `--apply` → dry-run only (report and stop)

Always **dry-run first, apply after user reviews**. Never apply on first
invocation.

## Step 1 — Scan candidates

```bash
grep -lE "^> 状态: (backlog|proposed)$" backlogs/*.md tasks/*.md 2>/dev/null | sort
```

If 0 candidates → report "no stale headers" and exit success.

## Step 2 — For each candidate, classify

For each candidate file:

### 2a. Extract sprint ID

Two sources to try, in order:

1. **Filename slug** — strip date/time prefix and `.md`:
   ```
   backlogs/2026-05-18-1203-tr-task-git-track-directory.md
   → tr-task-git-track-directory
   ```

2. **First heading in file** — `# TR-TASK-GIT-TRACK-001 · ...`:
   ```bash
   head -1 <file>
   ```
   Extract leading identifier (e.g. `TR-TASK-GIT-TRACK-001`,
   `ZF-LH-SP-001`, `EVAL-DECISION-OUTCOME-001`, `B-NEW-19`, `ω-1.a`).

Use the **fuller ID** (with `-001` suffix) for git log grep; use the
**short slug** as fallback. zaofu sprint ID patterns:

| Family | Example | Notes |
|---|---|---|
| `TR-*` | `TR-EVENT-SCHEMA-LOCK-001` | Trellis borrow |
| `EVAL-*` | `EVAL-DECISION-OUTCOME-001` | doc 43 evaluation methods |
| `PWF-*` / `ZF-PWF-*` | `ZF-PWF-PRECOMPACT-001` | doc 41 PWF supplement |
| `LH-*` / `ZF-LH-*` | `ZF-LH-SP-001` | doc 39/40 long-horizon |
| `B-NEW-NN` | `B-NEW-13` | cangjie-driven bug |
| `ω-N.x` / `α-N` / `β-N` | `ω-1.c`, `α-2` | doc 36-38 zero-touch |
| `PREREQ-X` | `PREREQ-B` | doc 40 prerequisite |

### 2b. Cross-reference

```bash
# Match A: explicit ID in git log subject
git log --since="$SINCE" --oneline | grep -iE "<sprint_id_full>|<sprint_id_short>"

# Match B: ID referenced in current src/ (uncommitted feature)
grep -rln "<sprint_id_full>" src/zf/ 2>/dev/null | grep -v __pycache__ | head -3
```

### 2c. Classify

| Match A non-empty | Match B non-empty | Class | Evidence |
|---|---|---|---|
| ✓ | (any) | **DONE-COMMITTED** | commit hash + subject(取最近的) |
| ✗ | ✓ | **DONE-UNCOMMITTED** | src paths(最多 3 个)|
| ✗ | ✗ | **TRUE-DEFER** | (none — needs human trigger)|

### 2d. Edge cases

- **No clear sprint ID** (e.g. INDEX 文件、批量索引、汇总文件) — classify
  as `META` and skip. Don't try to update meta files via this command.
- **Sprint ID 在 src/ 但只在注释里出现** — still treat as DONE-UNCOMMITTED
  (the implementation referenced it).
- **多个 commit 命中** — pick most recent.

## Step 3 — Report (always run)

Print a table:

```
| File | Class | Evidence |
|---|---|---|
| backlogs/2026-05-18-1203-tr-foo.md | DONE-COMMITTED | abc1234 "feat: TR-FOO-001 — ..." |
| backlogs/2026-05-18-1203-tr-bar.md | DONE-UNCOMMITTED | src/zf/core/bar.py, src/zf/cli/bar.py |
| backlogs/2026-05-18-1203-tr-baz.md | TRUE-DEFER | (needs trigger condition) |
| backlogs/2026-05-18-1300-eval-index.md | META | (skipped) |
```

Then a summary line:

```
Scanned N candidates: 18 DONE-COMMITTED, 3 DONE-UNCOMMITTED, 6 TRUE-DEFER, 0 META.
```

**If `--apply` not passed**:
```
Dry-run only. To apply DONE updates (DEFER stays untouched), rerun with `--apply`.
```

**If `--apply` passed**: continue to Step 4.

## Step 4 — Apply (only when --apply)

For each **DONE-COMMITTED** file:
```bash
sed -i "s|^> 状态: backlog$|> 状态: ✅ DONE (<utc-date> commit \`<short-hash>\` \"<subject-truncated-80>\")|" <file>
# Also handle "proposed" variant:
sed -i "s|^> 状态: proposed$|> 状态: ✅ DONE (<utc-date> commit \`<short-hash>\` \"<subject-truncated-80>\")|" <file>
```

For each **DONE-UNCOMMITTED** file:
```bash
sed -i "s|^> 状态: backlog$|> 状态: ✅ DONE (<utc-date>, UNCOMMITTED in working tree) — <src-paths-joined>|" <file>
sed -i "s|^> 状态: proposed$|> 状态: ✅ DONE (<utc-date>, UNCOMMITTED in working tree) — <src-paths-joined>|" <file>
```

For each **TRUE-DEFER**: **DO NOT EDIT**. Just print in report.

For each **META**: **DO NOT EDIT**.

**Escape carefully**: subject lines may contain quotes / shell metacharacters.
Use sed with `|` delimiter (as above) since paths/subjects may contain `/`.
If subject contains `|`, fall back to Python or Edit tool for that file.

## Step 5 — Verify (always run, even on dry-run)

```bash
remaining=$(grep -lE "^> 状态: (backlog|proposed)$" backlogs/*.md tasks/*.md 2>/dev/null | wc -l)
```

After dry-run: `remaining` = total stale (N).
After `--apply`: `remaining` should equal `TRUE-DEFER count + META count`.

If remaining is unexpected:
- print warning
- list which files still have stale headers
- do NOT crash — exit with diagnostic

## Step 6 — DEFER 后续动作建议

In the report, for TRUE-DEFER files, list a one-liner for each:

```
DEFER (need human trigger condition):
  - backlogs/2026-05-18-1203-tr-foo.md
    Suggest: 编辑加 "🟡 DEFER (<date> audit) — 触发条件: <X>"
```

Don't try to auto-fill the trigger — it requires judgment about WHEN
this work should be reconsidered (e.g. "当 multi-operator 启动时再做",
"当 PRD agent 立项后做",  "等 r-next-N 复发时做"). Bad auto-fills
become silent permanent DEFERs.

## Wire-up sanity

After Step 5, also run:
```bash
ls -la .claude/commands/audit-backlogs.md
grep -c "audit-backlogs" .claude/rules/backlogs.md
```

If `audit-backlogs` is NOT referenced from `.claude/rules/backlogs.md`,
that's expected (the command lives in `.claude/commands/`; the rule
just documents the recipe).

## Examples

```
/audit-backlogs                             # dry-run, since 2 weeks ago
/audit-backlogs --since "1 month ago"       # wider window
/audit-backlogs --apply                     # after reviewing dry-run
/audit-backlogs --since "1 week ago" --apply
```

## Self-check before committing audit results

Operator (or follow-up Claude session) should:
1. `git status backlogs/ tasks/` — check what changed
2. `git diff` any modified file — confirm sed didn't corrupt anything
3. Commit with `docs: audit backlogs — <N> DONE updates from <date>`
