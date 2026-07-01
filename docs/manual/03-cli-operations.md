# ZaoFu CLI 操作手册

> 适用对象: 日常操作、排障、测试和自动化脚本编写。

源码 checkout 下命令示例统一写成:

```bash
uv run zf <command>
```

第一次使用前运行 `uv sync --extra dev`;需要 Web/provider optional 依赖时运行
`uv sync --extra dev --extra web --extra stream-json`。如果已经安装 `zf`,
可把前缀替换为 `zf`。

## 1. 初始化与配置

| 命令 | 用途 |
|---|---|
| `zf presets` | 列出可用 preset |
| `zf presets show <name>` | 输出 preset YAML |
| `zf init [--preset NAME] [--state-dir PATH] [--force]` | 初始化运行态,可按 preset 生成 `zf.yaml`,并补齐项目根指令文档 |
| `zf init --skip-instruction-docs` | 只初始化运行态,不创建/刷新 `AGENTS.md` / `CLAUDE.md` |
| `zf validate --path zf.yaml` | 校验配置 |
| `zf validate --cold-start` | 5 点冷启动检查和 workflow topology 诊断 |
| `zf validate --strict-skills` | skill 缺失/冲突直接失败 |
| `zf validate --strict-contracts` | 检查 active task 的严格 contract |
| `zf validate --architecture` | 根据 `ARCHITECTURE_RULES.md` 执行架构规则检查 |
| `zf validate --instructions` | lint 指令文件 |

推荐启动前检查:

```bash
uv run zf validate --path zf.yaml
uv run zf validate --cold-start
uv run zf skills doctor
uv run zf doctor
```

## 2. 启停 Harness

| 命令 | 用途 |
|---|---|
| `zf start --dry-run` | 记录启动命令,不启动真实 tmux/provider |
| `zf start --foreground` | 启动 tmux workers 并在前台运行 watcher |
| `zf stop` | 优雅停止 session |
| `zf stop --force` | 强制停止 session 并清理 lock |
| `zf restart` | 重启 harness |
| `zf attach` | attach 到 session/pane |
| `zf logs` | 查看日志 |
| `zf status` | 查看 session/task 状态 |
| `zf status --workers` | 查看 worker 状态 |

生产式长任务建议用 `start --foreground`。非 foreground 模式会创建 tmux session,但 event watcher 不会可靠地长期驻留。

## 3. 任务与 Feature

| 命令 | 用途 |
|---|---|
| `zf chat "..."` | 写入 `user.message`,唤醒 orchestrator |
| `zf feature add <title>` | 创建 feature |
| `zf feature list [--status STATUS]` | 列出 feature |
| `zf feature show <feature_id>` | 查看 feature |
| `zf feature update <feature_id> ...` | 更新 feature |
| `zf kanban` | 列出 active tasks |
| `zf kanban --board` | 看列式 board |
| `zf kanban --watch --board` | 终端 watch board |
| `zf kanban add <title>` | 新建 task |
| `zf kanban add F-xxx <title>` | 新建并关联 feature |
| `zf kanban assign <task_id> <role>` | 分配 task |
| `zf kanban move <task_id> <status>` | 移动状态 |
| `zf kanban show <task_id>` | 查看 task 详情 |
| `zf kanban ready` | 查看 ready tasks |
| `zf kanban open` | 查看非终态 tasks |
| `zf kanban pending` | 查看 backlog tasks |
| `zf task trace <task_id>` | 查看 task 因果链 |

脚本创建 task:

```bash
TASK_ID="$(
  uv run zf kanban add \
    "补一个回归测试" --key "regression-test" --id-only
)"
uv run zf kanban assign "$TASK_ID" dev
```

## 4. 事件

| 命令 | 用途 |
|---|---|
| `zf events --last N` | 查看最近 N 条事件 |
| `zf events --type TYPE` | 按事件类型过滤 |
| `zf events trace <event_id>` | 查看事件 causation chain |
| `zf emit <type> --task <id> --payload JSON` | 手动追加事件 |
| `zf watch --last N --follow` | tail `events.jsonl` |
| `zf watch --role ROLE` | 按 actor 过滤 |
| `zf watch --task TASK_ID` | 按 task 过滤 |

示例:

```bash
uv run zf events --type dev.build.done --last 10
uv run zf emit dev.blocked \
  --task "$TASK_ID" \
  --actor dev \
  --payload '{"reason":"缺少复现步骤"}'
```

严格 preset 中,worker 的完成事件通常需要携带 dispatch token 和 contract evidence。手工 `emit` 适合诊断,不应作为绕过流程的常规手段。

## 5. Skills

| 命令 | 用途 |
|---|---|
| `zf skills list` | 查看每个 role 启用的 skill、来源和物化路径 |
| `zf skills list --json` | JSON 输出 |
| `zf skills doctor` | 检查缺失、无效、冲突 skill |
| `zf validate --strict-skills` | 把 skill warning 升级为启动前失败 |

常用:

```bash
uv run zf skills list
uv run zf skills doctor
```

## 6. Workdir、Refs 与 Runs

| 命令 | 用途 |
|---|---|
| `zf doctor workdirs` | 检查 workdir 健康 |
| `zf doctor panes` | 检查 pane-grid role 与 tmux pane 绑定 |
| `zf panes doctor` | 同上,作为 pane 绑定专用入口 |
| `zf panes repair` | 从 live tmux pane 的 workdir 恢复 `@zf_instance_id` 与 `.zf/pane_bindings.json` |
| `zf workdir repair <instance>` | 修复指定 worker workdir |
| `zf refs verify` | 检查 task/candidate refs |
| `zf runs list` | 查看 run archive 投影 |
| `zf runs rebuild` | 重建 run archive 投影 |
| `zf runs reconcile` | 标记 stale active runs |
| `zf runs for-task <task_id>` | 查看某个 task 的 runs |
| `zf archive-run ...` | 将 live state 归档到 `.zf/runs/<run_id>` |

建议在真实 E2E 或长任务结束后运行:

```bash
uv run zf refs verify
uv run zf doctor panes
uv run zf runs rebuild
uv run zf metrics snapshot
```

## 7. Gate、成本与指标

| 命令 | 用途 |
|---|---|
| `zf gate list` | 列出 `quality_gates` |
| `zf gate run <name>` | 运行单个 gate |
| `zf gate run all` | 运行所有启用 gate |
| `zf cost` | 查看成本汇总 |
| `zf cost --by-instance` | 按实例拆分成本 |
| `zf cost --by-backend` | 按 backend 汇总成本 |
| `zf metrics snapshot` | 输出 long-horizon 指标快照 |
| `zf metrics snapshot --format json` | JSON 指标 |
| `zf metrics snapshot --diff baseline.json` | 与 baseline 对比 |

## 8. Web 与外部集成

| 命令 | 用途 |
|---|---|
| `zf web --host 127.0.0.1 --port 8001` | 启动本地 dashboard |
| `zf web --host 0.0.0.0 --port 5175` | 对容器/局域网暴露 dashboard |
| `zf feishu ...` | 飞书适配相关命令 |
| `zf autopilot tick` | deterministic proposal-only 自检 |
| `zf self-eval ...` | 自评估命令 |
| `zf autoresearch ...` | 真实场景 research/eval runner |

`0.0.0.0` 只应在可信网络或本地 Docker 测试中使用。

## 9. Runtime State 清理

| 命令 | 用途 |
|---|---|
| `zf state clean --dry-run` | 查看可清理的 rebuildable projection |
| `zf state clean --confirm --archive` | 清理投影,保留 truth files |

`state clean` 会保留 `events.jsonl`、`kanban.json`、`feature_list.json`、`session.yaml`、`role_sessions.yaml` 这类 truth files。执行真实清理前,确认 harness 未运行且 evidence 已归档。
