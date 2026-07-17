# ZaoFu / 造父

### Harness More. Yoke Less.

> 面向 long-horizon 软件交付的 AI Agent Delivery Control Plane。

**Developer Preview · Python 3.11+ · Apache 2.0 · Codex + Claude Code**

[English README](README.md) · [产品演示](#产品演示) · [快速开始](#快速开始) ·
[核心能力](#核心能力) · [工作原理](#工作原理) ·
[产品入口与操作面](#产品入口与操作面) · [文档](#文档)

ZaoFu 把孤立的 coding-agent session 组织成一支可治理的交付团队。它不替代
Codex、Claude Code 或其他 provider CLI，而是为它们提供角色、任务契约、运行
上下文、持久化接力、证据门、恢复路径和确定性控制边界。

```text
普通 coding agent
  prompt -> agent 写代码 -> agent 自己说 done

ZaoFu 交付
  idea / PRD / issue / refactor
    -> intake / run goal
    -> scan / plan / task map
    -> multi-agent implementation + self-check
    -> independent verification
    -> Thin Judge / completion gate
    -> scoped ship / rework / escalation

任务恢复
  failure / stall
    -> Supervisor sensing -> Run Manager bounded action
    -> post-verify -> resume / owner escalation

Harness 自我改进
  recurring failure fingerprint
    -> Autoresearch reproduction / evidence-backed diagnosis
    -> isolated repair proposal / backlog candidate
    -> verify -> human or token-gated controlled apply
```

ZaoFu 的承诺不是“模型更聪明”，而是让 AI-agent 软件交付变得
**可配置、可观察、可恢复、可审计、证据门控**。

## 产品演示

<p align="center">
  <a href="https://raw.githubusercontent.com/uisee-ai/zaofu/main/assets/readme/zaofu-v1-github-720p-small.mp4">
    <img src="assets/readme/zaofu-v1-github-720p-preview.png" alt="打开 ZaoFu 产品演示" width="900">
  </a>
</p>

一次浏览 Dashboard、交付循环观测、多 Agent Channel、Kanban Agent 与飞书交互。
[打开视频](https://raw.githubusercontent.com/uisee-ai/zaofu/main/assets/readme/zaofu-v1-github-720p-small.mp4)。

## 快速开始

在 ZaoFu 源码 checkout 中安装常用开发与运行依赖，然后检查目标项目的产品
Controller 推荐结果：

```bash
cd /path/to/zaofu
uv sync --extra dev --extra web --extra stream-json

uv run zf profile bootstrap /path/to/my-project \
  --intent build --backend codex --scale launch
```

把推荐结果作为人工审批点。确认它是 `examples/prod/controller/` 中的 `[flow]`
entry，并审核目标项目真实 quality gates 和 Controller inputs 后再继续。

完整的 apply、初始化、cold-start、dry-run、正式启动和首个 workflow request
流程见[快速开始手册](docs/manual/01-quickstart.md)。

## 为什么需要 ZaoFu

Coding agent 已经很擅长局部代码生成，更困难的是长程交付中的工程控制：

- 任务逐渐偏离最初目标；
- 并行 worker 相互覆盖、重复工作或阻塞；
- “完成”只是聊天声明，而不是经过验证的状态迁移；
- evidence 散落在 terminal、transcript 和 worktree 中；
- 重复故障变成盲目重试，没有沉淀为可复用诊断；
- 操作员需要进度、风险、成本和交付证明，而不是逐条阅读 provider transcript。

ZaoFu 适用于多模块产品交付、大型重构与迁移、带回归证据的 issue 修复、质量
加固，以及多个 coding agent 必须收敛到同一 verified goal 的其他工作流。
它不是通用工作流自动化平台，也不是单 agent coding assistant。

## 核心能力

- **产品 Controller**：`examples/prod/controller/` 下的短 YAML 编译为
  canonical roles、stages、pipelines、schema policy、skill bundles、预算和
  recovery policy。项目本地 `zf.yaml` 始终是唯一有效控制面配置。
- **契约化多 Agent 执行**：Impl 和 Verify 固定不可变的 TaskContract snapshot。
  Worker 返回结构化 result、evidence 和 artifact refs，不依赖 transcript-only
  handoff。
- **类型化 Call / Return 与无损返工**：选定的 fanout 和 control call 接纳
  typed result envelope，在同一 attempt 修复格式错误、重放 settled operation，
  并把负向验证结果返回正确的 implementation owner。
- **证据门控 Goal Closure**：独立 Verify 进入 Thin Judge synthesis；
  deterministic completion gate 复核 claim 后，scoped delivery 才产生唯一
  goal 终态。
- **受控恢复与自我改进**：Supervisor sensing 和 Run Manager 仲裁负责有界任务
  恢复。Autoresearch 复现重复 failure pattern，并生成隔离、可验证的 repair 或
  backlog candidate。
- **操作员可见交付**：Web、Kanban、Agent Sessions、Delivery Trace、Inbox、
  Channel、CLI 和 Feishu 提供可重建 projection，并请求 token-gated controlled
  action。

## 工作原理

```text
                         zf.yaml
                      唯一控制面配置
                            |
                  profile / flow compiler
                            |
                            v
┌────────────────────────────────────────────────────────────────┐
│ Deterministic kernel + Orchestrator runtime                     │
│ dispatch / identity / schemas / mechanical gates / replay       │
│ stores / controlled actions / transitions / external effects   │
└───────────┬───────────────────────^─────────────────────┬───────┘
            | briefing + contract    | facts / intent      | truth
            v                        |                     v
┌────────────────────────────┐       |       ┌─────────────────────────────┐
│ Agent and skill layer      │───────┘       │ Read and operator surfaces  │
│ plan / impl / verify /     │               │ SQLite / Web / CLI / Feishu │
│ Thin Judge / provider CLIs │               └─────────────────────────────┘
└────────────────────────────┘
                            ^
                            | observe / request controlled action
┌───────────────────────────┴────────────────────────────────────┐
│ Run recovery: Supervisor -> Run Manager -> post-verification    │
│ Harness improvement: Autoresearch -> proposal -> verify/apply   │
└─────────────────────────────────────────────────────────────────┘
```

控制边界是刻意设计的：

| 角色 | 权限边界 |
|---|---|
| Kernel / Orchestrator runtime | 确定性 dispatch、identity、机械 gate、replay、状态迁移和外部副作用 |
| Workers + skills | 规划、实现、review、诊断和产品判断 |
| Supervisor | 观察、关联和发出 attention，不直接修复 |
| Run Manager | 选择 bounded recovery action，并要求 post-verification |
| Autoresearch | 复现重复 failure pattern 并提出 isolated repair，不直接应用 |
| Web / CLI / Feishu | 读取 projection，并请求 token-gated controlled action |

运行权威分层保存，而不是塞进一个巨大文件：

- `events.jsonl` 是 append-only 的发生、顺序、因果、判定与引用账本；
- kernel-managed Task、Feature 和 Session stores 保存当前操作状态；
- hash-addressed artifacts 和 sidecars 保存 plan、task map、evidence、diagnostic
  与大语义 payload；
- SQLite read model 加速 Timeline、Graph、Loop、Inbox、Channel 和 Agent Session
  查询，但不成为第二控制面。

主要闭环保持分离并可组合：

| 闭环 | 形态 |
|---|---|
| Delivery | `intake -> plan -> task map -> impl -> verify -> Thin Judge -> completion gate -> ship` |
| Quality | `contract snapshot -> typed result -> evidence gate -> pass or negative handoff` |
| 任务恢复 | `failure/stall -> Supervisor -> Run Manager action -> post-verify -> resume/escalate` |
| Harness 自我改进 | `recurring fingerprint -> Autoresearch -> isolated proposal -> verify -> controlled apply/backlog` |
| 人工审批 | `plan hold -> Web/Feishu approve or reject -> fanout unlock or repair` |
| 可观测性 | `event/store/artifact refs -> SQLite projection -> Web/CLI -> controlled action` |

## 产品入口与操作面

### Controller Catalog

`examples/prod/controller/` 是唯一面向用户的产品 YAML catalog：

| Controller | 使用场景 |
|---|---|
| `prd-fanout-v3.yaml` | 多 lane PRD 和产品功能交付 |
| `prd-light-v3.yaml` | 可以容纳在单上下文的小型 PRD |
| `issue-fanout-v3.yaml` | issue、bug 与 regression 修复 |
| `refactor-lane-v3.yaml` | 大型重构、迁移与替代式实现 |
| `*-claude.yaml` | 同一组 Controller 的 Claude Code 变体 |

Controller 选择与组合规则见 [catalog guide](examples/prod/controller/README.md)。

### 操作面

- **Web Dashboard**：Kanban、任务详情、Agent Sessions、Channel、Inbox、
  plan approval、Delivery Trace、Runs、Graph、Loop、evidence 和 controlled
  action。参见 [Web、观测与 E2E](docs/manual/06-web-observability-e2e.md)。
- **CLI**：配置、workflow intake、task/event/trace 查询、恢复、projection
  diagnostic、workspace 操作和 provider preflight。参见
  [CLI 操作手册](docs/manual/03-cli-operations.md)和
  [CLI 命令参考](docs/manual/09-zaofu-cli-usage.md)。
- **Feishu / ChatOps**：Channel、Kanban Agent、Run Manager 决策、流式 provider
  conversation 和 Plan Ready 审批卡片，共用 Web 的 controlled-action 边界。
  参见 [Feishu 直连 Bridge 手册](docs/manual/19-feishu-ai-native-direct-bridge.md)。

### 任务恢复与 Autoresearch

Run Manager 和 Autoresearch 闭合不同反馈环。Run Manager 通过 bounded
controlled action 恢复一次失败或停滞的 run；Autoresearch 在隔离 scenario 中
评估重复或长期未解决的 failure fingerprint，并生成 evidence-backed diagnosis、
repair proposal 或 backlog candidate。

两者都不能直接修改 kernel truth 或主线代码。Attempt 与预算有界，repair work
隔离执行，结果必须验证，apply 需要人工或 token-gated 授权。参见
[Autoresearch 手册](docs/manual/10-autoresearch-usage.md)。

## 文档

| 从这里开始 | 文档 |
|---|---|
| 首个项目与首个 request | [快速开始](docs/manual/01-quickstart.md) |
| Kernel、状态与生命周期模型 | [架构总览](docs/manual/architecture.md) |
| 日常操作命令 | [CLI 操作手册](docs/manual/03-cli-operations.md) |
| Web 与交付观测 | [Web、观测与 E2E](docs/manual/06-web-observability-e2e.md) |
| 故障诊断 | [故障排查](docs/manual/07-troubleshooting.md) |
| Harness 评估与修复 | [Autoresearch](docs/manual/10-autoresearch-usage.md) |
| 全部主题 | [使用手册索引](docs/manual/00-index.md) |

## 安全边界

- `zf.yaml` 是唯一控制面配置。
- Runtime state 属于 `project.state_dir`，不要提交 `.zf/`。
- Agent 只能报告 fact、artifact、evidence 和 intent，不能直接修改 kernel truth。
- Web/API/Integration 只能通过确定性、token-gated action path 修改状态。
- 产品 quality gate 和 acceptance semantics 必须来自目标项目，ZaoFu 不会虚构。
- Provider CLI 可能修改文件并消耗模型预算，真实执行前先 validate 和 dry-run。
- 除非网络明确可信，否则 Dashboard 只绑定 loopback。
- 共享主机上不要使用 `tmux kill-server` 停止单个项目。

## 名字由来

“造父”是中国古代传说中善御八骏的御者。强大的 coding agent 提供速度，ZaoFu
提供缰绳、路线、信号、节奏、恢复和验收边界，让团队持续朝同一个交付目标前进。

`Harness More. Yoke Less.` 表示通过可执行契约、证据和恢复边界组织 agent 能力，
同时减少不必要的流程负担。

<p align="center">
  <img src="assets/readme/zaofu-bajuntu.png" alt="造父驾驭八骏完成长途远行" width="900">
</p>

## 状态与许可证

ZaoFu 目前是 implementation-active Developer Preview。稳定版本之前，公开
Python API、Web API、event schema 和 Controller profile 仍可能调整。请验证
当前 checkout，不要依赖历史行为。

项目采用 [Apache License 2.0](LICENSE)，同时受[免责声明](DISCLAIMER.md)约束。
