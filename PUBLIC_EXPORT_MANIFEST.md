# ZaoFu Public Export Manifest

- Source ref: `77c187d81b7b66f1306ca1217e06c254149f50bc`
- Generated UTC: `2026-07-08T01:48:17Z`

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
