# ZaoFu Public Export Manifest

- Source ref: `d8f71269c50447daa78ce77a3a6086b3828e4b04`
- Generated UTC: `2026-07-13T04:40:43Z`

## Included

- `README.md`
- `README.zh-CN.md`
- `LICENSE`
- `DISCLAIMER.md`
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
- `yoke`
- `channel_roles`
- `docs/manual`

## Explicitly Excluded

- git history and private branches
- `.claude/`, `.codex/`, `.zf/`, runtime state, caches, and local env files
- all `docs/` subtrees except `docs/manual/`
- `backlogs/`, `tasks/`, `prompt/`, `prompts/`, `ideas/`, `reports/`, `slides/`
- project-specific `skills/cangjie-*`

## Required Manual Checks Before Publishing

- Confirm `LICENSE` and `DISCLAIMER.md` owner/copyright attribution before publishing.
- Review README links after public-only docs filtering.
- Run secret scanning before pushing to a public remote.
