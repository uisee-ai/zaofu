# ZaoFu Public Export Manifest

- Source ref: `ecf85bd15f37164a990fe71d9ffd5e9817238d9c`
- Generated UTC: `2026-07-01T03:34:56Z`

## Included

- `LICENSE` (copied from source working tree)
- `README.md`
- `README.zh-CN.md`
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
- `docs/manual`

## Explicitly Excluded

- git history and private branches
- `.claude/`, `.codex/`, `.zf/`, runtime state, caches, and local env files
- all `docs/` subtrees except `docs/manual/`
- `backlogs/`, `tasks/`, `prompt/`, `prompts/`, `ideas/`, `reports/`, `slides/`
- project-specific `skills/cangjie-*`

## Required Manual Checks Before Publishing

- Confirm `LICENSE` owner/copyright attribution before publishing.
- Review README links after public-only docs filtering.
- Run secret scanning before pushing to a public remote.
