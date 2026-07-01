---
name: zf-harness-commit-push
description: "Safe ZaoFu commit and push workflow for Claude or Codex. Use only when the user explicitly asks to commit, push, or wrap up approved changes. Inspects git state, separates unrelated dirty files, checks for secrets and risky staging, uses conventional commit messages, stages explicit paths only, never force-pushes, and reports verification."
---

# ZaoFu Harness Commit Push

## Objective

Commit and push approved ZaoFu changes without sweeping in unrelated work,
leaking secrets, bypassing hooks, or violating repository rules.

Default repository-facing output is Chinese unless the user asks otherwise.

## Preconditions

- Use this skill only after an explicit user request to commit and/or push.
- Read `AGENTS.md` commit rules before acting.
- Do not commit analysis-only notes, unapproved backlog candidates, or unrelated
  dirty files.
- Never use `git add -A`, `git add .`, `git commit --amend`,
  `git commit --no-verify`, `git push --force`, or `+refs`.
- If the diff is too broad to understand, stop and summarize what needs user
  confirmation before committing.

## Workflow

1. Inspect:
   - `git status --short --branch`
   - `git diff --stat`
   - `git diff --name-only`
   - `git log --oneline @{u}..HEAD` when an upstream exists
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
   - for skill-only changes, run `quick_validate.py` on every changed skill;
   - if validation is not run, state the exact reason.
5. Commit:
   - choose one conventional prefix: `feat:`, `fix:`, `docs:`, `style:`,
     `refactor:`, `test:`, or `chore:`;
   - stage explicit paths with `git add -- <path> ...`;
   - commit once with a concise message.
6. Push:
   - run `git push`;
   - if no upstream exists, use `git push -u origin HEAD`;
   - never force-push.

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
- pushed branch or push blocker;
- staged paths summary;
- validation run and result;
- unrelated dirty files intentionally left out.

How to test: ask "使用 zf-harness-commit-push 提交并 push 当前已批准的 skill 改动。"
