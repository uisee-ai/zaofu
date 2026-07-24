# ZaoFu 快速开始

> 适用对象：首次为已有项目物化 production Controller，并按最短安全路径跑通
> 端到端流程的操作者。

本手册只使用 `examples/prod/controller/` 下的产品 Controller catalog。
通用 preset 不是这里描述的产品工作流入口。一个项目保持一份 project-local
`zf.yaml` 和一个配置的 `project.state_dir`；后续 PRD、issue、feature 或 refactor
通过新的 workflow request 进入同一个项目控制面。

ZaoFu 源码仓库根 `zf.yaml` 默认是 PRD 工作流；`zf project init` 则默认创建
multi-kind Project 且不点火。完整的创建、Bootstrap、澄清与批准步骤见
[20 Project 创建、Bootstrap 与 Workflow 点火](20-project-bootstrap-workflow-ignition.md)。

## 0. 开始之前

必需环境：

- Python 3.11+
- `uv`
- Git
- `tmux`
- 至少一个已登录的 provider CLI：`codex` 或 `claude`

在 ZaoFu 源码 checkout 中安装依赖：

```bash
cd /path/to/zaofu
uv sync --extra dev --extra web --extra stream-json
uv run zf --version
```

Claude Code stream-json transport 需要 `stream-json`；只有使用本地 Dashboard
时才需要 `web`。

启动真实 worker 前检查 provider：

```bash
command -v tmux
command -v codex      # 使用 --backend codex 时
command -v claude     # 使用 --backend claude-code 时

uv run zf doctor provider --backend codex
```

Provider 登录状态由外部 CLI 管理。二进制缺失或登录失败时，先完成安装和认证。

## 1. 检查 Controller 推荐结果

设置稳定的源码和目标项目路径：

```bash
export ZAOFU_ROOT=/path/to/zaofu
export TARGET_PROJECT=/path/to/my-project
```

先只检查推荐，不写入文件：

```bash
uv run --project "$ZAOFU_ROOT" zf profile bootstrap \
  "$TARGET_PROJECT" \
  --intent build \
  --backend codex \
  --scale launch
```

Intent 对应产品工作流家族：

| Intent | 典型 Controller 家族 |
|---|---|
| `build` | PRD 交付（`prd-fanout-v3` 或 light 变体） |
| `refactor` | lane-based refactor（`refactor-lane-v3`） |
| `maintain` / `review` | issue 与 regression flow（`issue-fanout-v3`） |

推荐结果是人工审批点。沿用本产品路径时，只有 `archetype` 是
`examples/prod/controller/` 中的 `[flow]` entry 才继续。标记为 `[preset]`
的推荐是通用 fallback，不是 production Controller catalog。

Greenfield 项目代码信号不足时，使用 Web New Project wizard 显式选择 Controller，
并补充目标项目真实的 quality checks。

## 2. 物化并审核 Controller

确认推荐结果后执行：

```bash
uv run --project "$ZAOFU_ROOT" zf profile bootstrap \
  "$TARGET_PROJECT" \
  --intent build \
  --backend codex \
  --scale launch \
  --apply
```

物化会把选定 Controller 写成项目本地 `zf.yaml`，并复制所需 profile 和 skill
assets。`zf.yaml` 始终是唯一有效控制面配置。

如果项目已经存在 `zf.yaml`，bootstrap 会保留它，只补充可探测且未配置的检查，
不会静默把既有项目切换到另一个 Controller。继续前应显式审核或迁移当前控制面。

启动前审核：

- `prdRef`、`issueRef`、`sourceRoot`、`targetRoot` 等 Controller inputs；
- `project.state_dir` 与 worktree policy；
- provider backend 与 permission policy；
- `quality_gates` 是否是目标项目真实可执行的命令；
- validation 报告的 placeholder 或缺失环境要求。

Bootstrap 可以填入可探测的检查，但不能虚构产品语义和验收标准。多 lane
Controller 缺少项目 quality gate 时会 fail closed。

## 3. 初始化、验证与 Dry Run

从目标项目执行命令，确保相对路径基于它的 `zf.yaml` 解析：

```bash
cd "$TARGET_PROJECT"

uv run --project "$ZAOFU_ROOT" zf init \
  --workspace-register \
  --with-bootstrap

uv run --project "$ZAOFU_ROOT" zf validate --cold-start
uv run --project "$ZAOFU_ROOT" zf skills doctor
uv run --project "$ZAOFU_ROOT" zf workflow inspect
uv run --project "$ZAOFU_ROOT" zf start --dry-run --no-watch
```

Validation 仍有 `STOP` 时不要启动真实 provider。先修复缺失 route、skill、gate、
input 或工具，再重复上述检查。Dry run 只验证确定性启动 wiring，不代表 provider
已经登录，也不代表产品交付质量已经通过。

## 4. 启动与观测

启动 watcher 和 workers：

```bash
uv run --project "$ZAOFU_ROOT" zf start
```

Watcher 默认在前台运行，需要保持进程存活。另开一个终端：

```bash
cd "$TARGET_PROJECT"
uv run --project "$ZAOFU_ROOT" zf status --workers
uv run --project "$ZAOFU_ROOT" zf kanban --board
uv run --project "$ZAOFU_ROOT" zf events --last 30
uv run --project "$ZAOFU_ROOT" zf attach
```

## 5. 投递工作

首次可以提交自然语言目标：

```bash
uv run --project "$ZAOFU_ROOT" zf chat \
  "实现一个小功能，并提供测试、review 和交付证据。"
```

类型化产品 request 需要先生成 intake artifact。以下是 stock PRD route 示例：

```bash
uv run --project "$ZAOFU_ROOT" zf flow intake \
  --kind prd \
  --from docs/prd/account-security.md \
  --target-root app \
  --acceptance "账号安全验收测试通过" \
  --request-id prd-account-security \
  --output docs/intake/prd-account-security.md
```

输入仍有 open question 时先澄清并确认 requirement snapshot：

```bash
uv run --project "$ZAOFU_ROOT" zf flow clarify \
  --config zf.yaml \
  --intake docs/intake/prd-account-security.md \
  --confirm \
  --json
```

先预览 admission，不修改 runtime state：

```bash
uv run --project "$ZAOFU_ROOT" zf flow submit \
  --dry-run \
  --config zf.yaml \
  --intake docs/intake/prd-account-security.md \
  --kind prd \
  --allow-missing-env \
  --json
```

审核 preview 并解决环境要求后再 apply：

```bash
uv run --project "$ZAOFU_ROOT" zf flow submit \
  --apply \
  --config zf.yaml \
  --intake docs/intake/prd-account-security.md \
  --kind prd \
  --json
```

只有 `workflow.kind_routes` 声明的 request kind 才能进入项目。项目已在 route
中配置 pattern 时可以省略 `--pattern-id`。后续工作使用新的 `request_id`，
不要为同一个项目创建第二控制面。

## 6. 可选 Dashboard

从 ZaoFu checkout 启动：

```bash
tools/start-webkanban.sh --host 127.0.0.1 --port 8001
```

访问 `http://127.0.0.1:8001/`。Web mutation 需要生成或显式提供的 action token。
只有在可信网络中才绑定 `0.0.0.0`。

## 7. 停止

```bash
uv run --project "$ZAOFU_ROOT" zf stop
```

只有优雅停止失败时才使用 `zf stop --force`。共享主机上不要运行
`tmux kill-server`，只停止当前项目声明的 session。

## 下一步

- [架构总览](architecture.md)
- [CLI 操作手册](03-cli-operations.md)
- [Web、观测与 E2E](06-web-observability-e2e.md)
- [故障排查](07-troubleshooting.md)
- [Autoresearch](10-autoresearch-usage.md)
- [Feishu AI-Native 直连 Bridge](19-feishu-ai-native-direct-bridge.md)
