# ZaoFu Public Export Manifest

- Source ref: `0665608dc62e63b4db60934e46dd8e9a15b1ebd1`
- Generated UTC: `2026-07-17T07:56:06Z`

## Included

- `AGENTS.md`
- `CLAUDE.md`
- `zf.yaml`
- `README.md`
- `README.zh-CN.md`
- `LICENSE`
- `DISCLAIMER.md`
- `assets/readme`
- `.python-version`
- `pyproject.toml`
- `uv.lock`
- `src`
- `web`
- `examples`
- `tests`
- `tools`
- `scripts`
- `skills`
- `channel_roles`
- `.claude/rules`
- `.claude/commands/audit-backlogs.md`
- `docs/manual`

## Explicitly Excluded

- git history and private branches
- `.claude/` local settings, worktrees, and generated skill copies; only
  reviewed rules and `commands/audit-backlogs.md` are included
- `.codex/`, `.zf/`, runtime state, caches, and local env files
- all `docs/` subtrees except `docs/manual/`
- `backlogs/`, `tasks/`, `prompt/`, `prompts/`, `ideas/`, `reports/`, `slides/`
- project-specific `skills/cangjie-*`

## Required Manual Checks Before Publishing

- Confirm `LICENSE` and `DISCLAIMER.md` owner/copyright attribution before publishing.
- Review README links after public-only docs filtering.
- Run secret scanning before pushing to a public remote.
