# ZaoFu Public Export Manifest

- Source ref: `e277134cade5e9411603768612d8e50bb0163ffa`
- Generated UTC: `2026-07-24T08:54:31Z`

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
- `.claude/skills`
- `.codex/skills`
- `docs/manual`

## Explicitly Excluded

- git history and private branches
- `.claude/` local settings and worktrees; reviewed rules, the maintenance
  command, and tracked skill mirrors are included
- `.codex/` local state except tracked skill mirrors; `.zf/`, runtime
  state, caches, and local env files
- all `docs/` subtrees except `docs/manual/`
- `backlogs/`, `tasks/`, `prompt/`, `prompts/`, `ideas/`, `reports/`, `slides/`
- project-specific `skills/cangjie-*`

## Required Manual Checks Before Publishing

- Confirm `LICENSE` and `DISCLAIMER.md` owner/copyright attribution before publishing.
- Review README links after public-only docs filtering.
- Run secret scanning before pushing to a public remote.
