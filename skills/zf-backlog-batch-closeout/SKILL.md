---
name: zf-backlog-batch-closeout
description: "ZaoFu backlog/task batch closeout workflow for Codex or Claude. Use when the user asks to implement approved backlogs, finish a backlog batch, check whether a batch is done, commit completed backlog work, or archive completed tasks according to AGENTS.md/CLAUDE.md. Moves approved backlog candidates into tasks, verifies implementation, commits without pushing, then records the implementation commit hash before archiving done tasks."
---

# ZaoFu Backlog Batch Closeout

## Objective

Turn an approved backlog batch into verified code, local commits, and archived
`tasks/` records without pushing or sweeping unrelated work into history.

Default repository-facing output is Chinese unless the user asks otherwise.

## Preconditions

- Read `AGENTS.md` and follow ZaoFu backlog, task, commit, and verification
  rules before acting.
- Use this skill only after the user has approved implementation of specific
  backlog/task items or asks to close out a completed batch.
- Do not turn analysis-only discussion, vague ideas, or unapproved candidates
  into active tasks. Put those in `backlogs/` or `ideas/` only when requested.
- Never push. If the user asks to push, switch to the commit/push skill after
  this closeout is complete.
- Never stage unrelated dirty files. If the worktree contains unrelated
  changes, list them and leave them unstaged.

## Batch Model

Use this lifecycle:

```text
backlogs/<item>.md   --approval--> tasks/active/<item>.md
implementation       --verify-->   implementation commit
tasks/active/<item>.md --hash-->    tasks/done/<item>.md
archive metadata     --commit-->   archive commit
```

Two commits are preferred because `done` task records require the short hash
and title of the implementation commit:

1. implementation commit: code/docs/tests for the approved work;
2. archive commit: task files updated to `> 状态: done` and moved to
   `tasks/done/`.

If the approved batch is docs/task-file-only and there is no implementation
diff, make one archive commit and clearly say why there is no implementation
commit.

## Workflow

1. Identify approved scope:
   - list exact backlog/task files and source design docs;
   - reject ambiguous "everything" if unrelated proposed items are present;
   - keep unapproved items in `backlogs/`.
2. Activate:
   - `backlogs/` is gitignored (`.gitignore:48`), so typical candidates are
     untracked — `git mv` fails there (`fatal: not under version control`).
     Move with `mv backlogs/<file>.md tasks/active/` then
     `git add tasks/active/<file>.md`;
   - only for the rare already-tracked file does `git mv backlogs/<file>.md
     tasks/active/` apply;
   - if a task is already in `tasks/active/`, keep its path;
   - preserve UTC filename and existing content.
3. Implement:
   - make the requested code/docs/test changes;
   - keep runtime state out of git;
   - update relevant design/impl/manual docs when behavior changes.
4. Verify:
   - run focused tests or validation for changed behavior;
   - for skill-only changes, validate changed skills when a validator exists;
   - record exact commands and results for task archival.
5. Implementation commit:
   - inspect `git status --short --branch`, `git diff --stat`, and relevant
     diffs;
   - stage only implementation files and active task files if activation must
     be recorded with the implementation;
   - commit with a conventional prefix;
   - do not push.
6. Archive:
   - get `git log -1 --oneline`;
   - update each completed task's first paragraph to `> 状态: done`;
   - include the short hash + commit title and a concise verification summary;
   - move completed files to `tasks/done/` with `git mv`.
7. Archive commit:
   - stage only task archive files;
   - commit with `docs:` or `chore:` as appropriate;
   - do not push.
8. Report:
   - implementation commit, archive commit, and branch;
   - task files moved to `tasks/done/`;
   - verification commands and outcomes;
   - unrelated dirty files left untouched;
   - any items intentionally deferred.

## Safety Rules

- Never use `git add -A` or `git add .`.
- Never use `git commit --amend`, `git commit --no-verify`, or force push.
- Do not mark a task done if acceptance criteria are not verified.
- Do not hide failed verification. Either fix, defer with a concrete trigger,
  or ask the user to decide.
- Before marking a new orchestration component done, prove it is wired into a
  runtime caller such as `src/zf/runtime/orchestrator*.py` or
  `src/zf/cli/start.py`.
- Before fixing stale backlog bugs, reproduce against current HEAD. If no
  longer reproducible, mark the task verified-resolved rather than changing
  code.
- Keep `docs/`, `ideas/`, `prompt/`, runtime state, and dotfile policy aligned
  with repo rules and `.gitignore`; do not commit gitignored local candidates.
- Multi-driver git discipline (CLAUDE.md hard rule, 2026-06-11): when other
  sessions may be driving in parallel, work on `wip/<driver>-<utc-date>-<slug>`
  rather than committing straight to `dev`; before each `dev` commit re-check
  `git log -1` — if `HEAD` moved unexpectedly, another driver is active, so
  switch to a work branch instead of committing on `dev`. (See yoke/git-evidence
  for evidence-before-commit method.)
- dev pre-merge gate (CLAUDE.md hard rule, 2026-07-04): if this batch's commits
  are to land on `dev`, first run `bash scripts/dev-premerge-gate.sh` (<60s
  contract sentinels); red means do not merge. It does not replace full
  regression — it only blocks the classes most likely to redden a merge.

## When To Stop

Stop and ask for direction when:

- the approved backlog item is not identifiable;
- unrelated dirty files overlap the files you must edit;
- verification requires secrets, external services, or expensive real-provider
  runs not approved by the user;
- the implementation diff includes risky credential, runtime-state, or
  generated-file changes;
- acceptance criteria are impossible or contradict the current design.

## Output Shape

Use a concise Chinese closeout:

```text
已完成本批次 closeout。

实现 commit: <sha> <title>
归档 commit: <sha> <title>
归档任务: tasks/done/<file>, ...
验证: <command> -> pass
未纳入: <unrelated files or none>
未 push。
```

How to test: ask "使用 zf-backlog-batch-closeout 关闭这批已批准 backlog，完成后只 commit 不 push。"

## Maintenance

This skill ships as three independent copies (not symlinks):
`skills/`, `.claude/skills/`, and `.codex/skills/`
`zf-backlog-batch-closeout/SKILL.md`. Any edit here must be mirrored to all
three or the copies silently drift — or convert them to single-source
distribution.
