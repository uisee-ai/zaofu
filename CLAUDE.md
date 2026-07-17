@AGENTS.md

# ZaoFu — Claude Code Supplement

<!-- Canon lives in AGENTS.md (imported above). Anything restated here will
drift. Add rules there; keep this file to Claude-specific routing/context.
Keep this file short and avoid provider-neutral policy duplication. -->

`AGENTS.md`(上方已 import,随会话自动加载)是 provider-neutral 权威规则源;
本文件只放 Claude 特有的路由与上下文,不复述规则。

## Quick Reference

- Config: `zf.yaml`(唯一控制平面);runtime state: `project.state_dir`(默认 `.zf/`,gitignore)
- CLI: `zf`(pyproject `zf.cli:main`);命令清单看 `zf --help`
- Design docs 入口: `docs/design/00-index.md`;Examples: `examples/`

## Lazy-Loaded Rules (`.claude/rules/*.md`)

Path-scoped rules auto-load only when Claude touches matching files:

| File | `paths:` glob | Content |
|---|---|---|
| `.claude/rules/code.md` | `src/**`, `tests/**` | Code conventions, module size discipline, wire-up anti-pattern, changeset simplicity |
| `.claude/rules/backlogs.md` | `backlogs/**`, `tasks/**` | Sprint/backlog conventions, status field, audit recipe, validate-first |
| `.claude/rules/docs.md` | `docs/**` | Sub-directory semantics, numbering discipline, 00-index registration, orphan check, idea→design→impl flow |

To force-load: `@.claude/rules/code.md`.

## Architecture

Common architecture and runtime interaction rules live in `AGENTS.md`
§Architecture / Runtime Route so Claude and Codex share the same context.
Use `docs/design/00-index.md` for the full index and verify behavior against
`src/` and tests before implementing.

## Commands

命令清单看 `zf --help`;这里只留 help 查不到的运行套路与约定:

- `uv sync --extra dev --extra web` -- 安装完整本地测试依赖
- `zf validate --cold-start` -- 冷启动 5-point readiness check
- `zf web --port 8001` -- 主端口 8001 留给真 dev session,模拟一律 8002+
  (见 AGENTS.md §Temporary Simulation Hygiene)
- `uv run pytest <focused-paths> -q --no-cov` -- 修改后的确定性聚焦测试
- `uv run pytest -q --no-cov` -- 仓库全量;当前包含 host 版本/能力 sensor,
  并可能调用已安装 provider CLI,必须单独分类环境基线,不等于真实 E2E
- `bash scripts/dev-premerge-gate.sh` -- 合 dev 前哨兵门(规则见
  AGENTS.md §Multi-Driver Git Discipline)

## Discipline Health Signals

这些规则生效的可观察标志(对照 audit 时看,不是抽象合规):

- `git diff --stat <sprint commit>` 行数 ≈ 任务复杂度本身,**不是 4×**
- `/audit-backlogs` 跑出来 DONE-stale 占比 ≤10%(本周首跑是 77%,反向 case)
- `zf validate` 失败时有可追溯的 event/store/sidecar diagnostic ref(silent stall = 0)
- 新文件首版 ≤1000 行,**无后续"切分"refactor commit**
- Sprint acceptance criteria 用 `step → verify`,weak verification 出现 = 0
- backlog 立项 → tasks/ 完成 → status: done + commit hash 的链路 ≥ 90% 文件遵守

观察指标恶化 → 走 audit + 修规则,不是怪 LLM 不听话。
