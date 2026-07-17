# ZaoFu Public Export Manifest

- Source ref: `9e32d7035040e67ae62e789f91cd736c1db70225`
- Generated UTC: `2026-07-17T06:02:19Z`

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
