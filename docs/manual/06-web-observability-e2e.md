# Web Dashboard 使用指南

[English](06-web-observability-e2e.en.md) · [手册首页](00-index.md)

> 面向使用 ZaoFu 管理 Project、Task、Workflow、Agent 和交付证据的操作者。
> 浏览器测试、真实 Provider smoke 与发布验证已拆到[维护者验证指南](operations/web-maintainer-validation.md)。

## 1. 启动与选择 Project

安装依赖并使用 canonical launcher：

```bash
uv sync --extra dev --extra web
tools/start-webkanban.sh --host 127.0.0.1 --port 8001
```

launcher 统一处理 Web build、action token、Workspace/provider 环境、Codex headless sandbox、
tmux 和重启。只在可信网络为了容器或远程浏览器改用 `--host 0.0.0.0`。

常用生命周期：

```bash
tools/start-webkanban.sh --port 8001 --status
tools/start-webkanban.sh --host 127.0.0.1 --port 8001 --no-build
tools/start-webkanban.sh --port 8001 --stop
```

如果只需查看指定 state dir，可以使用低层入口：

```bash
uv run zf web --state-dir /tmp/zf-run/.zf --host 127.0.0.1 --port 8002
```

低层入口不会补齐 launcher 的 trusted-local provider 环境。需要 Channel/Kanban Agent 的真实
Provider 操作时优先使用 launcher。

页面顶部先选择 Project。Project 是所有 Task、Run、Channel 和 projection 的作用域；切换后
确认 Project 名称、Live 状态和 URL query 同时变化，再开始操作。

## 2. Workspace：完成日常操作

左侧 Workspace 是“我要处理什么”：

| 页面 | 主要用途 | 关键判断 |
|---|---|---|
| Overview | Project 目标、任务流、成本和健康摘要 | 当前最重要的风险/进度是什么 |
| Inbox | 需要 owner 裁决的 proposal、异常和通知 | 哪个项目语义或动作需要人决定 |
| Tasks | Kanban、Task contract 和详情 | Goal/PRD/Issue/Refactor 现在在哪一步 |
| Workflows | Needs decision、Active、History | 哪个 exact proposal 可批准，哪个 Run 在执行 |
| Agents | role/provider/worker、usage、cost、skills、context | 谁在做什么，是否 stuck 或接近上下文边界 |
| Automations | Daily/Weekly/Project Monitor 等计划任务 | 自动化何时触发、最近一次是否成功 |

推荐操作顺序：Overview 识别状态 -> Inbox 处理必要裁决 -> Tasks 进入具体交付 -> Workflows
确认启动或运行状态 -> Agents 只在需要定位执行者时下钻。

## 3. Tasks：从看板读到证据

Tasks 默认展示 Kanban。选择一张 Task 后，详情按以下层次阅读：

| Tab | 查看内容 |
|---|---|
| Summary | objective、contract、owner、依赖、当前 stage/status |
| Activity | Task timeline、attempt、dispatch、rework 和关键事件 |
| Evidence | artifact refs、测试、git evidence、verdict 和完成依据 |
| Advanced | 原始合同、诊断、引用和低层运行时信息 |

Task 卡片状态不是完成证明。至少把 Goal/Claim、当前 attempt、required artifacts、verification、
terminal verdict 和 git evidence 串起来。需要全链路时从 Task 进入 Delivery 或执行：

```bash
uv run zf kanban show TASK-ID
uv run zf task trace TASK-ID
uv run zf runs for-task TASK-ID
```

新建 Task、选择 Workflow、批准 proposal 是不同动作。Task 存在不代表 Workflow 已启动；
Plan/route 选定也不等于 Approve。

## 4. Workflows：只批准 exact proposal

Workflows 将生命周期分为：

- **Needs decision**：等待 operator 查看 objective、route、parameters 和风险；
- **Active**：已批准的 Run、当前 stage 和等待原因；
- **History**：已关闭、拒绝、失败或被替换的 proposal/Run。

批准前核对 Task、route、objective、input refs 和参数是否与当前需求一致。Approve 只能绑定 exact
proposal；修改任何语义后应生成新 proposal。外部副作用必须通过 action token/trusted session
的受控路径，Provider Agent 不能接触 token。

Workflow 页显示当前 Requirement、已绑定 Task 和 Run；从 Task 链接进入时，优先选择与 Task
身份精确匹配的 Request。大图默认只展示重点节点并明确提示省略数量，可展开完整图；缩略
结构可能隐藏中间关系，不应解读为不存在依赖。节点详情的 Loop 链接携带 Request 和 Run；
Loop 当前只展示最近 Run，若不匹配会提示，不会把它冒充所选 Run。

## 5. Delivery：读完整 Workflow

Monitoring 下的 **Delivery** 以 Feature/交付为作用域，页内有三个 mode：

| Mode | 回答的问题 |
|---|---|
| Overview | 当前 ship readiness、Task/Run 总览和主要 blocker 是什么 |
| Runs | Run graph 如何推进，Task 的 attempt、gate、event、evidence 和 regression 是什么 |
| Graph | Goal -> Claim -> canonical Task 是否在 Plan、Implementation、Verification、Closure 四轴闭合，还有哪些 Gap/currentness 问题 |

先选择正确 Feature，再看状态/ship/drift/replan 指标。Runs 中区分 transport delivery、Worker result、
gate verdict 和 closure；它们不是同一件事。Runs 只有单 Run + Inspector，不再复制
Stage Heatmap 或 delivery synthetic Span waterfall。Graph 只有单一轻量 coverage surface，
不以节点数量代替完成质量。需要 Span Tree/Waterfall 时进入 `Traces`；只有 verified
canonical Trace ref 才会从 Runs 显示深链。

Overview 的 ship readiness 由现有 graph/drift/ship 投影计算。Runs 中的 ship 是
`not_evaluated / summary_only`，即使 Task 全部 done 也不猜测 ready。Delivery v2 不提供
Latest Loop 摘要；查看 behavior/eval/improvement 时直接进入顶层 `Loop`。

Goal Dossier 把 Goal -> Claim -> Task -> Evidence -> Verdict 汇总为 owner 可验收结论。关闭交付前，
重点查看 mandatory Claim 覆盖、terminal Task、缺失 evidence、最新 generation 和 owner decision。

## 6. Loop、Traces 与 Operations

**Loop** 展示系统如何围绕反馈收敛，而不只是事件列表。它可包括 plan/execute/verify/rework、
GAN/critic、recovery、autoresearch 或 profile 定义的其他 loop。判断重点是每轮 gap 是否缩小、
证据是否增加，以及是否命中 no-progress/budget/replan 边界。

Loop 只加载自身的 scoped projection，不会为了这个页面额外读取 Delivery feature 列表。Task 有大量
attempt 时，drawer 首批展示 100 条并通过 **Load more** 继续展开；计数、Timeline、Completion
Promise 和 Business Loops 仍基于完整投影，不会把首窗误报成完整历史。semantic event 会有界刷新，
heartbeat/tick 等机械 pump 不触发页面请求。

Timeline 的 Observed attempts 只计入有 `attempt_id` 的内核或历史开始记录，终态按 Task 和
Attempt 身份配对；未经配对或其他运行时事件单独展示，不等同任务失败。历史事件窗口可能
缺少开始记录，数值是已观测下界，不是完整次数。旧版接口无该投影时显示 Legacy records。
Plan vs actual 仅比较当前 Run 中有引用和摘要的内核准入 Task Map 与已观察尝试；没有可信的
计划耗时和成本基线时，不计算进度偏差、ETA 或费用预测。

**Traces** 是低层因果诊断的 canonical 入口。列表按 Task、actor、status、duration、role 或
backend 筛选；选择一行后才并行读取 bounded detail 与 lifecycle spans。宽幅详情先显示可证明
的 Span Tree/Waterfall 和选中 Span Inspector，再保留 ZaoFu 的 Execution Route 与 Event
evidence；Raw 只在选中 evidence 后按需读取。当前 Span 仅来自 allowlisted kernel/runtime
started/terminal lifecycle pair，并明确 source、truth class、coverage 与 degraded 原因；它不把
Event、stage、causation 或 Delivery synthetic span 伪装成 provider-native LLM/tool Span。没有
可信 pair 时页面会说明 coverage 并回退到 Execution/Events。直接打开
`?page=traces&project=...&trace_id=...&span_id=...` 时，请求保持 project scoped，不会先加载
完整 dashboard snapshot；首窗外 Span 由同一 bounded response 定位，列表中的 `Load earlier
spans` 再按 cursor 展开历史。

Events、Event Logs、Runs、Fanouts、Candidates、Integration 和 Repair 是低频兼容诊断页。
Operations 继续提供 provider capability、OTLP exporter、SSE 与运行健康摘要，旧链接可用
`?page=observability&obs_tab=operations` 打开。Runtime Logs 不再有独立 Web panel，但已脱敏、
轮转的 store 和有界 HTTP API 保留；Raw 只在具体对象详情中按需展开。

正常验收优先使用 Tasks、Delivery 和 Goal Dossier；Traces/Operations 用于解释“为什么没继续”
或“哪个 projection/attempt 出了问题”。

### 可选的 OTLP、Provider telemetry 与 Operations

OTLP exporter 默认关闭，只有 `zf start` 的 runtime tick 会调度它；单独运行 `zf web` 只读取
已有状态，不会创建 exporter、collector 或额外后台线程。启用时只把环境变量名写进 `zf.yaml`
的 `ZfConfig.spec`（legacy 单文档则在根级）中：

```yaml
observability:
  otlp_exporter:
    enabled: true
    endpoint_env: ZF_OTLP_ENDPOINT
    headers_env: ZF_OTLP_HEADERS  # 可选：JSON object 的环境变量名
    batch_size: 64
    healthy_sample_rate: 0.1
  alerts:
    enabled: true
    cooldown_seconds: 300
```

endpoint/header 值只由受控 runtime 环境提供，例如 `ZF_OTLP_ENDPOINT` 与
`ZF_OTLP_HEADERS`；不要把 URL、Bearer token 或 header JSON 提交到 YAML、事件或截图。Operations
显示 health、backlog、上次成功/失败、采样/丢弃/脱敏计数和 SSE gap 摘要。它导出的是 ZaoFu
synthetic、脱敏 span，不在 Web 中回读 provider 原文 waterfall，也不改变 Delivery Graph、Gate 或
Task 状态。完整的操作、metrics token gate、Runtime Logs API、Provider 支持矩阵、canary 与回退见
[Metrics、Observability 与 Operations](21-metrics-observability-operations.md) 和
[Provider Native Telemetry 与 OTLP](22-provider-native-telemetry.md)。

![同一 playgroud 交付在 Delivery、Graph、Loop 与 Observability 间的观测路径](assets/observe-delivery.webp)

## 7. Channels：讨论不等于执行

从 Project rail 的 Channel 入口打开群组协作。Channel 支持人、Provider Agent、persona、
owner delegate 和 observer 在同一上下文中澄清模糊需求。

- 普通消息保持 conversation，不自动 fanout；
- 显式 Discuss 才进入 multi-lens relay/critique/synthesis；
- Finalize 生成 draft/canonical candidate，Owner confirm 才成为 Task/PRD 来源；
- 只有具备 `propose_workflow` 能力的 exact leader 可以提出 Workflow handoff；
- handoff 仍需独立审批，不会因讨论结束自动执行。

完整合同见 [Channel 协作](15-channel-collaboration.md)和
[Channel 到 PRD](workflows/channel-to-prd.md)。

## 8. Live、Degraded 与写操作

Web 是读取投影和受控动作表面，不是 canonical state owner：

- **Live**：SSE/轮询与当前 Project 正常同步；
- **Reconnecting**：保留已知快照，等待补齐 gap；
- **Degraded**：明确显示 projection/sidecar 缺失，不把旧数据伪装成实时；
- freshness 不满足时，先执行 projection/refs/doctor，再决定是否恢复 Run。

创建 Task、Channel member、Workflow apply、maintenance prepare、runtime resume 等写操作必须走
token/passcode/trusted-session gated controlled action，并留下审计事件。UI 成功 toast 之后仍应从
Task/Event/Workflow readback 确认动作已生效。

## 9. 交付签收路线

```text
Tasks: contract and current state
  -> Delivery: stages, attempts, and dependencies
  -> Goal Dossier: Claim coverage and evidence
  -> Inbox: unresolved owner decisions
  -> Observability: only when diagnosis is needed
```

终端交叉核验：

```bash
uv run zf kanban --board
uv run zf task trace TASK-ID
uv run zf refs verify
uv run zf metrics snapshot
uv run zf doctor
```

浏览器功能验证和真实 E2E 见[Web 维护与 E2E 验证](operations/web-maintainer-validation.md)。
