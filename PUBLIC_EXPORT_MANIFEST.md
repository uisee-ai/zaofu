# ZaoFu Public Export Manifest

- Source ref: `3af4a823bf044de737da7695fa378355cc4050c4`
- Generated UTC: `2026-07-23T00:21:35Z`

## Included

- `AGENTS.md`
- `CLAUDE.md`
- `zf.yaml`
- `feishu.yaml`
- `README.md`
- `README.zh-CN.md`
- `LICENSE`
- `DISCLAIMER.md`
- `assets/readme`
- `.python-version`
- `.env.example`
- `pyproject.toml`
- `uv.lock`
- `src`
- `web`
- `examples`
- `tests`
- `tools`
- `scripts`
- `skills`
- `yoke`
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
