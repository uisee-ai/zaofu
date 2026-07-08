# ZaoFu 快速开始

> 适用对象: 第一次在一个项目里启动 ZaoFu harness,或需要用最短路径验证当前仓库是否可运行的操作者。

> **最短可行路径(TL;DR)**
> ```bash
> # 0. 前置:后端 CLI + tmux 在 PATH 上(见 §0)
> command -v claude && command -v tmux        # claude-code 后端
> # 1. 装环境(safe-team/claude-code 真实运行需要 stream-json extra)
> uv sync --extra dev --extra stream-json && uv run zf --version
> # 2. 生成可运行配置(首跑请用 fresh project + 通过 dry-run 的 preset,见 §2)
> uv run zf init --preset safe-team
> # 3. 启动前检查
> uv run zf validate --cold-start && uv run zf start --dry-run --no-watch
> # 4. 启动 + 投递任务
> uv run zf start
> uv run zf chat "实现一个小功能并完成测试、review、judge 全流程"
> ```

## 0. 前置检查(Preflight)

启动真实 agent **之前**,先确认后端 CLI 与 tmux 已就位。**注意:`zf validate` / `zf preflight` 目前不检查后端二进制是否存在(P0-4),需手动确认** —— 否则 `zf start` 不会报 Python 错误,而是在 tmux pane 里静默变成 `command not found` 死 pane,表现为"任务派下去了但 worker 没动静"。

```bash
# 1) 后端 CLI 在 PATH 上(按 zf.yaml 里 orchestrator/roles 用的 backend)
command -v claude    # backend: claude-code
command -v codex     # backend: codex
command -v tmux      # 所有 backend 都需要(harness 跑在 tmux 里)

# 2) 后端已登录 / 真实可用
uv run zf doctor provider --backend codex   # codex 的真实可用性探测
claude --version                            # claude-code 自检(应能正常输出)
claude -p 'Reply with exactly: zaofu-ok' --output-format text --dangerously-skip-permissions

# 3) Python 环境就绪
uv run zf --version
```

任一项缺失,先安装 / 登录再继续。装好后端但忘了这一步,是首次运行最常见的"静默挂死"踩坑点。

## 1. 进入仓库

源码 checkout 下推荐用 `uv` 管理 Python 环境和依赖。第一次进入仓库先同步常用开发依赖。
如果后续要按 `safe-team` 启动真实 Claude Code stream-json 后端,不要只装
`--extra dev`;必须包含 `--extra stream-json`,否则运行时会报
`No module named 'claude_code_sdk'`。

```bash
cd /path/to/zaofu
uv sync --extra dev --extra stream-json
uv run zf --version
```

需要 Web dashboard 或 Feishu bridge 时,同步对应 optional extras:

```bash
uv sync --extra dev --extra web --extra stream-json --extra feishu
```

如果只需要复现锁定环境,使用:

```bash
uv run --locked zf --version
```

## 2. 生成或确认 `zf.yaml`

`zf.yaml` 是唯一控制面。已有项目应先查看现有 `zf.yaml`;新项目可以从 preset 生成。
注意: `zf init --preset ...` 不等于"覆盖当前仓库已有复杂 `zf.yaml`"。如果你在 ZaoFu
源码 checkout 或任何已有 `zf.yaml` 的目录里验证 quickstart,实际 dry-run 仍以当前
`zf.yaml` 为准;要验证 preset,请在 fresh project 里运行。

```bash
uv run zf presets
uv run zf init --preset safe-team
```

常用 preset 及其适用场景(2026-07-07 远端 fresh-project dry-run 实测):

| Preset | 适用场景 | fresh-project `zf start --dry-run --no-watch` |
|---|---|---|
| `safe-team` | orchestrator + arch/dev/review/test/judge 的标准三层架构 | ✅ 通过,首跑推荐 |
| `design-first` | design -> dev -> review/test/judge 的设计先行流 | ✅ 通过 |
| `minimal` | 只启动一个 dev worker 的最小 harness | ⚠️ STOP:`terminal_event_without_producer judge.failed` |
| `code-assist` | dev/review/test 的代码辅助流 | ✅ 通过 |
| `safe-local` | 本地单 dev,适合快速验证 CLI/runtime | ⚠️ STOP:`missing_rework_route static_gate.failed` + `judge.failed` |

不同 preset 的 topology 会随实现演进。启动前以当前代码的
`uv run zf start --dry-run --no-watch` 和 `uv run zf workflow inspect`
输出为准;出现 `STOP` 时先按诊断信息修配置,不要按旧手册假设 preset 固定失败。

当前仓库的真实 Codex 压测配置在 `examples/dev-codex-backends.yaml`,通常用于 E2E 和鲁棒性验证,不建议直接作为普通项目默认配置。

## 3. 完整初始化新项目

推荐新项目优先使用完整 bootstrap 脚本,而不是手动组合多条命令。脚本会:

- 生成或复制 `zf.yaml`
- 执行 `zf init`,并缺失时生成项目根 `AGENTS.md` / `CLAUDE.md`
- 注册 Workspace
- 将 `project.state_dir` 加入 `.gitignore`
- 当 `runtime.workdirs.mode=worktree` 时确保项目是 git repo 且有 HEAD
- 最后执行 `zf start --dry-run --no-watch` 做启动前检查

```bash
cd /path/to/zaofu
tools/init-project.sh \
  --project-dir /path/to/project \
  --preset safe-team \
  --yes
```

已有 `zf-codex.yaml` 等配置时:

```bash
tools/init-project.sh \
  --project-dir /path/to/project \
  --source-config /path/to/project/zf-codex.yaml \
  --yes
```

如果只想初始化 state,暂时不做启动 dry-run,加 `--skip-start-dry-run`。
当配置启用 `worktree` 且项目还没有 git HEAD 时,`--yes` 会允许脚本初始化 git
并把当前所有未忽略文件作为初始提交。去掉 `--yes` 时会进入人工确认。

## 4. 初始化运行态

初始化会创建 `project.state_dir` 指向的运行态目录,默认是 `.zf/`:

```bash
uv run zf init
```

新项目可直接指定路径并创建目录:

```bash
uv run zf init /path/to/project --create --preset safe-team
```

默认会尝试注册到 workspace project manager;需要显式控制时使用
`--workspace-register` / `--no-workspace-register`。需要初始化前环境探测时加
`--env-check`;不希望写 git hooks 时加 `--no-git-hooks`。

`zf init` 默认会创建/刷新项目根指令文件:

- `AGENTS.md`: provider-neutral 的项目规则、短 Harness Health Signals,并包含 ZaoFu managed worker protocol block。
- `CLAUDE.md`: Claude Code 入口说明,指向 `AGENTS.md`。

如只想初始化 runtime state,可用 `uv run zf init --skip-instruction-docs`。

如果 `zf.yaml` 里配置了非默认 `project.state_dir`,`init` 会优先使用该路径。强制重建时使用:

```bash
uv run zf init --force
```

注意: `--force` 会重新初始化运行态真相文件,包括 `events.jsonl` 和 `kanban.json`。执行前先确认需要保留的 evidence 已归档。

## 5. 启动前检查

启动真实 agent 前先做配置和冷启动检查:

```bash
uv run zf validate --path zf.yaml
uv run zf validate --cold-start
uv run zf validate --strict-skills
```

常用补充检查:

```bash
uv run zf doctor
uv run zf skills doctor
uv run zf gate list
```

## 6. Dry Run

`start --dry-run` 会走启动流程、生成指令/钩子/技能投影并记录命令,但不会真正启动 tmux workers:

```bash
uv run zf start --dry-run --no-watch
```

**读懂结果**:
- 无 `STOP` 输出 = dry-run 通过,可以启动真实 harness。
- 出现 `STOP terminal_event_without_producer` / `STOP missing_rework_route` 等拓扑错误时,
  先运行 `uv run zf workflow inspect` 看推导出的 `unhandled` / `orphan` / `dead-end`
  边,再修 `roles.triggers` / `roles.publishes` / `workflow.rework_routing`。
- 如果使用内置 preset 仍出现 STOP,以当前 dry-run 输出为准生成 bug/backlog;不要沿用旧手册里某个日期的 preset 状态判断。

Dry run 通过后,再启动真实 harness。

> 远端真实 E2E 注意(2026-07-07):fresh `safe-team` 能启动 9 个 tmux worker,Claude Code
> 能消费 `zf chat` 并创建 task;但首个真实任务可能停在 backlog,因为 orchestrator 角色的
> tool allowlist 过窄,而 briefing 要求它写 contract payload 文件再 emit。看到
> `agent.timeout` 或 `Claude requested permissions to write ... contract.json` 时,这是
> preset/allowlist 需要修复,不是安装步骤问题。

## 7. 启动真实 Harness

推荐直接启动 watcher,因为 watcher 负责事件唤醒、stuck/orphan/recycle 扫描和 orchestrator 推进:

```bash
uv run zf start
```

`--foreground` 仍被接受,但当前代码里只是 deprecated no-op alias;默认行为已经是在前台运行 watcher。
如果只想 spawn workers 后退出、不长期运行 watcher,才使用 `--no-watch`。

另开一个终端观察:

```bash
tmux attach -t zf
uv run zf kanban --board
uv run zf events --last 20
```

如果 `zf.yaml` 设置了 `session.tmux_session`,使用对应 session 名 attach。

## 8. 投递任务

给 orchestrator 发送用户目标:

```bash
uv run zf chat "实现一个小功能并完成测试、review、judge 全流程"
```

也可以手动创建 kanban task:

```bash
TASK_ID="$(
  uv run zf kanban add \
    "修复一个具体 bug 并补回归测试" --id-only
)"
uv run zf kanban assign "$TASK_ID" dev
```

严格链路下,`assign review/test/judge` 和 `move done` 会检查前置事件,不能绕过 dev -> review -> test -> judge 的证据链。

## 9. 停止

优先使用:

```bash
uv run zf stop
```

只有在 session 卡死、无法优雅停止时才使用:

```bash
uv run zf stop --force
```

不要用 `tmux kill-server`。如需手动处理 tmux,只关闭 `zf.yaml` 中 `session.tmux_session` 对应的 session。
