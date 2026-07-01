# ZaoFu / 造父

### Harness More. Yoke Less.

> 面向 long-horizon 软件交付的 AI Agent Delivery Control Plane。

ZaoFu 把 Claude Code、Codex 等 coding agent 从孤立的聊天会话，组织成一支可治理的软件交付团队。它不替代 coding agent 的代码能力，而是给这些 agent 提供角色、任务契约、运行上下文、证据要求、恢复机制和事件化控制平面。

[English README](README.md)

```text
普通 coding agent:
  prompt -> agent 写代码 -> agent 自己说 done

ZaoFu:
  idea / issue / refactor
    -> plan / task-map
    -> role workers
    -> evidence / review / verify
    -> deterministic gates
    -> done / rework / escalation
```

ZaoFu 的核心承诺不是“模型更聪明”，而是让 AI-agent 软件交付变得 **可配置、可观察、可恢复、可审计、可验证**。

---

## 名字由来

“造父”是中国古代传说中的御者，常被称为周穆王的首席驾驶员、御马之神。传说中，造父善御八骏，能够驾驭强大的马队完成长途远行。

这个名字对应 ZaoFu 的产品隐喻：

- coding agent 像一匹匹能力很强的马，速度快，但容易跑偏；
- ZaoFu 不替马奔跑，也不取代 agent 写代码；
- ZaoFu 负责缰绳、车辕、路线、节奏、信号和验收；
- 目标不是“绑住 agent”，而是让多 agent 在长程交付中持续朝正确方向前进。

## Harness More. Yoke Less.

`Harness More. Yoke Less.` 是 ZaoFu 的工程哲学。

这里的 **harness** 指 harness engineering：用确定性 kernel、事件账本、任务契约、证据门、恢复包、观测面和受控动作，把 agent 的能力接入真实软件工程流程。

这里的 **yoke** 指生硬的束缚、过度的流程负担和不可验证的 prompt 约束。ZaoFu 不希望靠更长的指令、更重的 gate 或更多人工盯盘来“勒住”agent，而是希望：

- 多用可执行的协议，少用口头约定；
- 多用事件和证据，少信自我宣告；
- 多用恢复和回放，少靠人工记忆；
- 多用清晰边界，少让 workflow、skills、chat 成为第二控制面；
- 多让 agent 发挥能力，少用不必要的约束制造 rework。

一句话：

> 造父不是给马加更多枷锁，而是设计一套能驾驭长途远行的车、路、信号和验收体系。

## 为什么需要 ZaoFu

AI coding agent 已经很擅长局部代码生成。真正难的是工程控制：

- 长任务会偏离最初目标；
- 多个 agent 会互相覆盖、重复工作或阻塞；
- “完成”经常只是聊天里的自我声明，不是可验证状态；
- review、test、runtime evidence 散落在 terminal 输出和对话里；
- 团队需要看到进度、风险、成本、blocker 和交付证据，而不是逐条读 transcript。

ZaoFu 的判断是：coding agent 应该被当作“有用但不完全可信的工程 worker”。Agent 可以计划、实现、review、test、报告意图；但 runtime truth 和状态迁移必须由确定性 kernel 接管。

## 适用场景

ZaoFu 适合已经在使用 coding agent，并希望给 agentic delivery 加上工程纪律的团队：

- 大型重构和迁移项目；
- 多模块产品功能交付；
- issue / bug 修复与回归测试；
- 测试覆盖率和质量加固；
- 长程 AI-native 工程工作流；
- 需要 Kanban、Trace、Feishu、Channel、Operator View 的团队协作。

ZaoFu 不是通用工作流自动化平台，也不是单 agent coding assistant。

## 核心能力

- **`zf.yaml` 控制面**：定义 roles、stages、triggers、providers、gates、budgets、recovery policy 和 workflow topology。
- **多 agent 执行**：orchestrator、architect、critic、dev、review、test、judge、Kanban Agent、Channel members 和 provider-backed workers。
- **事件化运行真相**：`events.jsonl`、`kanban.json`、`session.yaml`、`feature_list.json`、`role_sessions.yaml` 由 kernel 管理。
- **证据门控完成**：TaskContract、static gates、review/test/judge signals、discriminator checks 和 artifact refs。
- **长程恢复**：heartbeat、stuck detection、context recovery、bounded rework、Run Manager 和 Delivery Trace。
- **操作员控制台**：Web dashboard、Kanban、Agent sessions、Delivery Trace、Inbox、Channel、Feishu 和 projection-based observability。
- **自我改进闭环**：Supervisor、Autoresearch、run recovery、self-repair proposal 和 backlog synthesis。

## 架构模型

```text
                 zf.yaml
             唯一控制面配置
                    |
                    v
┌────────────────────────────────────────────────────────────┐
│ Layer 1: deterministic kernel                              │
│ EventLog / EventWriter / TaskStore / gates / projections   │
│ token-gated actions / recovery / state reconciliation      │
└───────────────────────┬────────────────────────────────────┘
                        |
                        v
┌────────────────────────────────────────────────────────────┐
│ Layer 2: orchestrator brain                                │
│ plan / split / route / replan / escalate                   │
│ 只能通过 zf CLI 或 controlled actions 写入 truth            │
└───────────────────────┬────────────────────────────────────┘
                        |
                        v
┌────────────────────────────────────────────────────────────┐
│ Layer 3: hands / workers                                   │
│ arch / critic / dev / review / test / judge / providers    │
│ 从 briefing 工作, 并 emit 结构化 evidence events            │
└────────────────────────────────────────────────────────────┘
```

核心不变量：

> Agent 可以提出方案和执行工作。ZaoFu 通过确定性 kernel 和 append-only events 决定状态。

## 主要业务闭环

ZaoFu 不是一个大 while loop，而是一组可组合的业务闭环和运行闭环：

| 闭环 | 形态 |
|---|---|
| Delivery | `idea -> plan -> task-map -> impl -> verify -> ship` |
| Quality | `evidence -> gate -> pass or bounded rework` |
| Human approval | `plan hold -> Web/Feishu approve -> fanout unlock` |
| Channel collaboration | `discussion -> synthesis -> workflow intent` |
| Kanban Agent | `operator request -> proposal/action -> projection` |
| Run recovery | `observe -> decide -> controlled action -> post-verify` |
| Autoresearch | `failure -> diagnosis/proposal -> gate -> repair path` |
| Replan | `drift/insight -> proposal -> contract eval -> adoption` |
| Module parity | `verify -> parity scan -> gap plan -> task-map amend` |
| Observability | `events -> projections -> Web/CLI -> controlled action` |

## 仓库结构

```text
src/zf/
  cli/                 zf CLI 入口
  core/                config、events、task stores、workflow graph
  runtime/             orchestrator/runtime loops、channels、run manager
  integrations/        Feishu 和外部集成边界
  autoresearch/        外层评估和自我改进闭环
  web/                 FastAPI app 和 read projections

web/                   React dashboard
examples/              workflow 和 provider 配置示例
docs/manual/           用户手册
tests/                 deterministic 和 E2E 测试
tools/                 本地运维脚本
```

运行态数据属于 `project.state_dir`，默认是 `.zf/`。它不是源码，不应该提交到 git。

## 环境要求

- Python 3.11+
- `uv`
- `tmux`
- 至少一个真实 worker 使用的 coding-agent provider CLI：
  - `codex`
  - `claude`
  - 或其它已配置 backend

可选：

- Node.js / npm：用于 Web dashboard 前端构建。
- Docker：用于 Playwright 浏览器 E2E。
- Feishu 凭证：用于 ChatOps 和审批卡片。

## 从源码安装

```bash
git clone <repo-url> zaofu
cd zaofu

uv sync --extra dev --extra web --extra stream-json
uv run zf --version
uv run pytest
```

只需要 CLI 的轻量环境：

```bash
uv sync --extra dev
uv run zf --help
```

## 快速开始

先生成或确认 `zf.yaml`，初始化 runtime state，做 validate 和 dry-run，再启动真实 harness：

```bash
uv run zf presets
uv run zf init --preset safe-team --workspace-register

uv run zf validate --cold-start
uv run zf start --dry-run --no-watch

uv run zf start
```

另开一个终端投递任务和观察状态：

```bash
uv run zf chat "实现一个小功能,要求包含测试和 review evidence。"
uv run zf kanban --board
uv run zf events --last 30
```

常用 preflight：

```bash
command -v tmux
command -v codex      # 使用 Codex 时
command -v claude     # 使用 Claude Code 时

uv run zf doctor provider --backend codex
uv run zf skills doctor
uv run zf workflow inspect
```

新项目或外部项目建议使用 bootstrap 脚本：

```bash
tools/init-project.sh \
  --project-dir /path/to/my-project \
  --preset safe-team \
  --yes
```

已有配置时：

```bash
tools/init-project.sh \
  --project-dir /path/to/my-project \
  --source-config /path/to/my-project/zf-codex.yaml \
  --yes
```

更多说明：[docs/manual/01-quickstart.md](docs/manual/01-quickstart.md)。

## Web Dashboard

本地开发推荐使用脚本启动。它会构建 `web/dist`，在 tmux 中启动 FastAPI dashboard，加载 `.env`，并写入或复用 Web action token：

```bash
tools/start-webkanban.sh
tools/start-webkanban.sh --status
tools/start-webkanban.sh --stop
```

默认地址：

```text
http://127.0.0.1:8001/
```

需要局域网或 Docker Playwright 访问时：

```bash
tools/start-webkanban.sh --host 0.0.0.0 --port 8001
```

只在可信网络绑定 `0.0.0.0`。Web mutation 通过 `ZF_WEB_ACTION_TOKEN` 或 `~/.zaofu` 下生成的 token file 进行门控。

更多说明：

- [docs/manual/06-web-observability-e2e.md](docs/manual/06-web-observability-e2e.md)
- [docs/manual/09-zaofu-cli-usage.md](docs/manual/09-zaofu-cli-usage.md)

## 常用命令

```bash
# 配置与启动
uv run zf validate --path zf.yaml
uv run zf validate --cold-start
uv run zf start --dry-run --no-watch
uv run zf start
uv run zf stop

# 运行观测
uv run zf status --workers
uv run zf kanban --board
uv run zf events --last 50
uv run zf watch --follow
uv run zf trace show <trace_id>

# 任务与证据
uv run zf kanban add "修复登录态过期 bug"
uv run zf task trace <task_id>
uv run zf runs for-task <task_id>
uv run zf gate list

# Web
tools/start-webkanban.sh --status
uv run zf web --host 127.0.0.1 --port 8001
```

完整 CLI 参考：[docs/manual/09-zaofu-cli-usage.md](docs/manual/09-zaofu-cli-usage.md)。

## Workflow 示例

| 示例 | 用途 |
|---|---|
| `examples/safe-team.yaml` | 标准多角色本地团队 |
| `examples/design-first.yaml` | 设计先行交付流 |
| `examples/dev-codex-backends.yaml` | 全 Codex 开发 smoke 拓扑 |
| `examples/dev-mixed-backends.yaml` | 混合 backend 压测拓扑 |
| `examples/zf-full-codex.yaml` | full Codex delivery DAG |
| `examples/prod/prd-fanout-codex.yaml` | PRD fanout 产品交付 |
| `examples/prod/issue-fanout-codex.yaml` | issue / bug fanout 交付 |
| `examples/prod/refactor-flow-codex.yaml` | refactor 交付流 |

使用前先 validate：

```bash
uv run zf validate --path examples/safe-team.yaml
```

## Feishu / ChatOps

ZaoFu 支持直连飞书 bridge：

```bash
uv sync --extra feishu
uv run zf feishu bridge --watch
```

bridge 可以把飞书消息路由到项目 Channel、Kanban Agent、Run Manager Agent 或 provider-backed coding-agent conversation。它也支持计划审批卡片，通过和 Web 相同的 controlled-action path 解锁 gated fanout execution。

更多说明：

- [docs/manual/19-feishu-ai-native-direct-bridge.md](docs/manual/19-feishu-ai-native-direct-bridge.md)
- [docs/manual/11-feishu-automation-kanban-sync.md](docs/manual/11-feishu-automation-kanban-sync.md)
- [docs/manual/15-channel-collaboration.md](docs/manual/15-channel-collaboration.md)

## Autoresearch 与鲁棒性

Autoresearch 是外层评估和自我改进闭环。它会用场景反复验证 ZaoFu，记录 evidence，识别 failure pattern，并生成 repair proposal 或 backlog candidate。

先 dry-run：

```bash
uv run zf autoresearch run \
  --scenario controlled-stuck-recovery \
  --worktree /tmp/zf-autoresearch-dry \
  --config examples/dev-codex-backends.yaml
```

真实 provider run 需要显式确认，并会消耗模型预算：

```bash
uv run zf autoresearch run \
  --scenario controlled-stuck-recovery \
  --worktree /tmp/zf-autoresearch-real \
  --config examples/dev-codex-backends.yaml \
  --expected-done 1 \
  --timeout 7200 \
  --budget-usd 180 \
  --tmux \
  --confirm
```

更多说明：

- [docs/manual/10-autoresearch-usage.md](docs/manual/10-autoresearch-usage.md)
- [docs/manual/autoresearch-orchestrator.md](docs/manual/autoresearch-orchestrator.md)
- [docs/manual/16-real-codex-provider-preflight.md](docs/manual/16-real-codex-provider-preflight.md)
- [docs/manual/18-product-fanout-real-e2e.md](docs/manual/18-product-fanout-real-e2e.md)

## 测试

快速本地检查：

```bash
uv run pytest
npm --prefix web run build
```

Web / Channel / Kanban Agent 交互审计：

```bash
tests/e2e/scripts/run_web_interactive_e2e_audit.sh --skip-docker
```

确定性鲁棒性套件：

```bash
tests/e2e/scripts/run_robustness_suite.sh --smoke
tests/e2e/scripts/run_robustness_suite.sh
```

真实 provider smoke：

```bash
tests/e2e/scripts/run_robustness_suite.sh \
  --include-real codex \
  --confirm-real
```

更多说明：[docs/manual/06-web-observability-e2e.md](docs/manual/06-web-observability-e2e.md)。

## 文档地图

建议从这里开始：

- [docs/manual/00-index.md](docs/manual/00-index.md) — 用户手册索引
- [docs/manual/architecture.md](docs/manual/architecture.md) — 架构总览
- [docs/manual/01-quickstart.md](docs/manual/01-quickstart.md) — 首次运行
- [docs/manual/02-zf-yaml-control-plane.md](docs/manual/02-zf-yaml-control-plane.md) — `zf.yaml`
- [docs/manual/03-cli-operations.md](docs/manual/03-cli-operations.md) — 日常操作
- [docs/manual/04-harness-runtime.md](docs/manual/04-harness-runtime.md) — runtime flow
- [docs/manual/13-plan-task-map-orchestrator-dispatch.md](docs/manual/13-plan-task-map-orchestrator-dispatch.md) — plan 到 task-map 再到 dispatch
- [docs/manual/14-delivery-trace-usage.md](docs/manual/14-delivery-trace-usage.md) — delivery trace


## 安全边界

- `zf.yaml` 是唯一控制面配置。
- runtime state 属于 `project.state_dir`，不要提交 `.zf/`。
- `events.jsonl` 是 append-only runtime truth。
- Web/API/Integrations 只能通过 token-gated controlled actions 或 deterministic kernel path 修改状态。
- Provider CLI 可能消耗预算并修改文件。真实 provider 执行前先 dry-run 和 preflight。
- 不要在不可信网络暴露 Web dashboard 或 Feishu bridge。

## 当前状态

ZaoFu 正处于 active implementation 阶段。确定性 kernel、CLI、runtime、Web dashboard、workflow examples、Feishu bridge、Channel、Run Manager 和 Autoresearch 路径都已经存在。API 和 workflow preset 仍在演进，因此使用新配置前应先 validate 和 dry-run：

```bash
uv run zf validate --cold-start
uv run zf start --dry-run --no-watch
```
