# `zf.yaml` 控制面与运行态

> 适用对象: 需要配置角色、技能、workdir、质量门禁、预算和恢复策略的操作者。

## 1. 基本原则

`zf.yaml` 是 ZaoFu 的唯一控制面。角色、事件、质量门禁、技能源、运行态目录、预算、安全策略都应该通过它声明。

不要引入第二套任务 schema 或外部控制面。外部系统只能通过事件、CLI 或明确的 kernel API 写入意图,不能直接改业务真相。

### 1.1 创建 Project 时为什么不再选择 YAML

Web `Add/Open Project` 不再让用户选择 YAML、Controller、kind、lane 或 role。这是
**入口简化**，不是移除 `zf.yaml`：

- 目录已有合法 `zf.yaml` 时，Inspect 只决定 open、register 或 initialize state，
  不会改写现有配置；
- 目录没有配置时，Initialize 生成一份默认 multi-kind `zf.yaml`；
- Project Brief、Stack 和 Provider 只补充项目上下文、命令 Profile 与 provider
  policy，不直接选择 Workflow；
- Kanban Agent 为已有 Task 推荐 Workflow 时，只能使用该 Project 当前
  `zf.yaml` 展开的 route catalog。

因此一个 Project 始终只有一份 canonical `zf.yaml`。需要显式采用单一 Controller
或迁移现有控制面时，使用 `zf profile bootstrap` / 配置迁移流程，并在写入前人工审核，
不要把 YAML 选择重新放回日常 Project 创建表单。

低层 `ZfConfig` 精简片段（不包含 Flow document/profile 展开）:

```yaml
version: "1.0"
project:
  name: my-project
  state_dir: .zf
session:
  tmux_session: zf
orchestrator:
  backend: claude-code
roles:
  - name: orchestrator
    backend: claude-code
    role_kind: reader
    triggers: [dispatch.silent_stall, orchestrator.rework.triage.requested]
    publishes: [orchestrator.rework.triage.recorded]
  - name: dev
    backend: claude-code
    permission_mode: bypass
    triggers: [task.assigned]
    publishes: [dev.build.done]
```

## 2. 顶层字段

| 字段 | 作用 |
|---|---|
| `version` | 配置版本,当前常用 `1.0` |
| `preset` | 人类可读的 preset 名,不替代实际字段 |
| `project.name` | 项目名,也用于部分 session/报告显示 |
| `project.state_dir` | 运行态目录,默认 `.zf`;命令应优先解析它 |
| `session.tmux_session` | tmux session 名 |
| `session.tmux_layout` | `window_per_role` 或 `pane_grid` |
| `orchestrator` | Python runtime loop 与兼容 Agent wake 的 backend、turn、timeout、冷却配置；不单独决定 authority |
| `roles` | worker 与显式 exception-advisor role 列表 |
| `providers` | workspace/provider 绑定和 provider-specific 配置入口 |
| `integrations` | 飞书、外部 channel/bridge 等集成配置 |
| `skill_sources` | skills 来源目录 |
| `runtime` | workdir、git isolation、skills materialize 配置 |
| `workflow` | rework routing、wake extension、stages、pipelines、fanout/fan-in 等流程配置 |
| `quality_gates` | 外部命令门禁 |
| `verification` | contract/scope/architecture/promoted 等 discriminator 开关 |
| `autopilot` | deterministic proposal-only 自检 |
| `autoresearch` | trigger、review gate、resident/loop 相关配置 |
| `goal` | goal/evaluation 相关配置 |
| `security` | event signing 等安全配置 |
| `global_budget_usd` | 全局预算硬上限 |

判断一个字段是否真实生效时,以 `src/zf/core/config/schema.py` 和 runtime 引用为准。设计文档中仍保留了一些未落地意图。

Product Flow 中，Kernel 根据 topology/profile 做快乐路径机械派发。显式 `orchestrator` role
默认订阅异常分诊事件；只有配置 `workflow.orchestration.mode: semantic_control` 时，才会在
预注册 checkpoint 上执行 shadow/blocking 语义判断。Legacy safe-team 仍是另一种兼容模式。
`orchestrator` 配置块、Python `WorkflowRuntimeCoordinator`（兼容 alias 为 `Orchestrator`）和
同名 role Agent 是三个需要分别核对的对象。

## 3. Role 配置

角色的 `name` 是 role type,`instance_id` 是展开后的 worker 实例 ID。`replicas > 1` 时,例如 `name: dev, replicas: 4`,会展开为 `dev-1` 到 `dev-4`。

常用字段:

| 字段 | 作用 |
|---|---|
| `name` | 角色类型,如 `orchestrator`、`arch`、`dev`、`review`、`test`、`judge` |
| `backend` | `claude-code`、`codex`、`python` 等 |
| `backends` | 多副本时按副本指定 backend,长度必须等于 `replicas` |
| `model` | 留空表示使用 provider CLI 默认模型 |
| `permission_mode` | `bypass`、`allowlist`、`default` 或 `restricted` |
| `allowed_tools` | allowlist 模式下的工具白名单 |
| `transport` | 默认 `tmux`;`stream-json` 也是有效 transport，适用于已注册的 headless/自动化路径 |
| `replicas` | 静态副本数 |
| `role_kind` | `auto`、`writer`、`reader`;配合 workdir/git isolation |
| `skills` | 当前角色启用的 skill 名 |
| `plugins` / `agent` | Claude-only 扩展;Codex 会忽略或只消费注入文本 |
| `provider_session` | role-scoped Provider 原生 `effort`、`agent`、`max_parallel_agents`；启动时冻结 digest |
| `lifecycle` | Provider process/pane 生命周期：`eager`、`resident`、`on_demand` |
| `triggers` | 该角色被哪些事件唤醒或分配 |
| `publishes` | 该角色允许发布哪些阶段完成/失败事件 |
| `stuck_threshold_seconds` | pane 输出无变化多久算 stuck |
| `orphan_warning_seconds` / `orphan_escalate_seconds` | in-progress 任务无阶段进展的 warning/escalation 时间 |
| `max_rework_attempts` | 同一任务最大返工次数 |
| `context_window_tokens` / `context_warning_threshold` / `context_compact_threshold` / `context_hard_cap` | 上下文 warning / compact / hard-cap 阈值;可通过 `.env` 变量插值设置 |
| `budget_usd` | 单角色预算硬上限 |

建议:

- `model` 默认留空,让 provider CLI 使用当前默认模型。
- Codex 自动化场景通常使用 `permission_mode: bypass`,避免交互式 approval 挂起。
  `default` / `restricted` 对应更保守的 provider 权限姿态,需要结合具体后端能力验证。
- 多副本 dev/test 应优先显式设置 `replicas`,不要复制多个同名 role。

### 3.1 Role-scoped Provider session 与按需生命周期

首期 Provider 动态配置绑定 role/session，不是每个 Task 临时选择一套 execution profile：

```yaml
roles:
  - name: orchestrator
    backend: codex
    role_kind: reader
    lifecycle:
      mode: resident
  - name: dev-lane-0
    backend: codex
    role_kind: writer
    provider_session:
      effort: high
      max_parallel_agents: 2
    lifecycle:
      mode: on_demand
      idle_seconds: 120
      cooldown_seconds: 30
      preserve_session: true
      preserve_workdir: true
```

未声明 `provider_session` 时继承 Provider 默认能力。显式配置会生成 immutable effective
session sidecar，并将 digest 绑定 `RoleSessionRegistry`；digest 改变时不能盲目 resume 旧
session。`on_demand` 只释放空闲 process/pane，逻辑 role、session/workdir/affinity 继续保留；
`orchestrator` role 禁止 `on_demand`。当前真实 Provider 扩大 rollout 尚未签收，因此先在
controller canary 中使用。

## 4. Skills 配置

典型组合:

```yaml
skill_sources:
  - name: agent-skills
    path: ${ZF_AGENT_SKILLS_DIR:-/path/to/external-skills-root/skills}
    mode: readonly
  - name: zaofu-local
    path: ${ZF_ZAOFU_SKILLS_DIR:-/path/to/zaofu/skills}
    mode: readonly
  - name: yoke-critic
    path: ${ZF_YOKE_CRITIC_DIR:-/path/to/role-gate-skills-root/role-skills/critic}
    mode: readonly

runtime:
  skills:
    pool: .zf/skills
    materialize: copy
    lock_file: .zf/skills.lock.json
    strict: false
```

角色只声明自己需要的 `skills`。启动时 ZaoFu 会解析来源、检测冲突、投影到 runtime workdir,并更新 `skills.lock.json`。

当 `agent-skills` 和 `yoke` 存在同名且能力相同的 skill 时,主线应优先使用 `agent-skills`;`yoke` 只作为 harness/role context/gate/evaluator 这类补充能力来源。

## 5. Workdir 与 Git Isolation

启用方式:

```yaml
runtime:
  workdirs:
    enabled: true
    root: .zf/workdirs
    mode: worktree
  git:
    writer_branch_prefix: worker
    task_ref_prefix: task
    candidate_branch_prefix: candidate
    candidate_base_ref: main
    candidate_strategy: cherry-pick
```

建议 role kind:

| Role | 建议 `role_kind` | 原因 |
|---|---|---|
| dev | `writer` | 需要改代码,应在隔离 worktree/branch 中执行 |
| review | `reader` | 应审查候选 ref,不直接写业务 truth |
| test | `reader` | 应验证候选 ref,不直接混入 dev worktree |
| judge | `reader` | 最终判定基于 evidence 和 git refs |
| orchestrator | `reader` / `auto` | Product Flow 异常 advisor 不应直接写代码或持有快乐路径状态机 |

## 6. Quality Gates 与 Verification

`quality_gates` 是 shell 命令门禁:

```yaml
quality_gates:
  static:
    enabled: true
    required_checks:
      - PYTHONPATH=src pytest -q
      - npm --prefix web test
```

`verification` 控制 deterministic discriminator:

```yaml
verification:
  contract:
    required: true
    quality_required: true
    rework_delta_required: true
    dispatch_token_required: true
  scope:
    fail_closed: true
  architecture:
    enabled: true
  promoted:
    enabled: true
```

严格项目建议开启 contract 和 scope fail-closed。这样 agent 提前宣告完成、缺少验证证据、绕过返工 delta、越界写入时,会被 kernel 阻断并路由返工。

### 6.1 OA checkpoint 与 Task Pipeline v4 canary

OA semantic checkpoint 必须显式配置，默认仍是 `exception_advisor`：

```yaml
workflow:
  orchestration:
    mode: semantic_control
    checkpoints: [plan_candidate]
    checkpoint_policies:
      plan_candidate: shadow
```

`shadow` 只记录 typed decision，不阻塞 Kernel。`blocking` 仅用于带明确 pilot/canary 边界的
PRD/Issue/Refactor profile；当前真实 canary 为 HOLD，不能作为项目默认。

Task Pipeline v4 也只通过 Flow document 显式启用：

```yaml
apiVersion: zaofu.dev/v1
kind: IssueFlow
spec:
  flowProfile: issue-flow-v4-task-pipeline
  topology: fanout
  taskPipeline:
    mode: ${ZF_TASK_PIPELINE_MODE:-shadow}
    maxActiveTaskPipelines: 4
    pools:
      impl: {capacity: 1, roleInstances: [fix-lane-0]}
      verify: {capacity: 1, roleInstances: [verify-lane-0]}
    workerLifecycle: {mode: on_demand, idleSeconds: 120}
    integrationAdmission:
      default: verify_admitted
      riskReview: {enabled: false}
    candidate:
      integration: incremental_serial_cas
      integrationCapacity: 1
      rollingSmoke: required
      partialCandidateAutoShip: forbidden
      finalVerifyTarget: frozen_exact_commit
```

合法 profile ID 只有 `issue-flow-v4-task-pipeline`、`prd-flow-v4-task-pipeline` 和
`refactor-flow-v4-task-pipeline`。仓库 canary 位于
`examples/prod/controller/*-task-pipeline-v4-canary*.yaml`，均为 `preferred: false`。
多 Flow 配置会为角色和 Task Pipeline 池引用统一加 Flow 前缀，池配置摘要绑定展开后的名称。
旧版已准入代次若仅因池名称未加前缀而产生摘要差异，运行时会验证旧摘要和角色的一一对应，
保留原准入摘要并记录绑定来源；其他策略漂移继续阻断，不能直接改写摘要。
v4 实现已完成但 rollout 仍 NO-GO；不要把 `shadow` 误解为已经接管业务调度，也不要在
active Run 中热改 mode/profile。

## 7. 运行态真相文件

运行态目录由 `project.state_dir` 决定。默认 `.zf/` 中的关键文件:

| 文件 | 说明 |
|---|---|
| `events.jsonl` | append-only 事件日志 |
| `kanban.json` | active task canonical state; terminal task 归档到 `kanban/` |
| `feature_list.json` | active feature canonical state; terminal feature 归档到 `feature_list/` |
| `session.yaml` | harness session canonical state |
| `role_sessions.yaml` | role instance 到 provider session 的 canonical mapping |
| `cost.jsonl` | 成本 projection |
| `skills.lock.json` | skill 解析/物化投影 |
| `instructions/` | 生成给各 role 的指令 |
| `workdirs/` | runtime workdir、skills manifest、隔离 checkout |
| `runs/` | E2E/真实 run archive |
| `projections/` | Web、Delivery、Supervisor、diagnostics 等可重建 projection |
| `fanouts/` | fanout child sidecar result / manifest |

不要手写 `events.jsonl`、`kanban.json`、`feature_list.json`、`session.yaml`、`role_sessions.yaml`。代码层应使用 `EventWriter`、`TaskStore`、`FeatureStore`、`SessionStore`。

## 8. 兼容性提示

大部分新 CLI 会解析 `project.state_dir` 或提供 `--state-dir`。使用自定义 state dir 时,
优先从项目根目录执行,并用 `zf validate --cold-start` / `zf start --dry-run --no-watch`
确认所有入口都解析到同一个 runtime state。

`zf.yaml` 同目录 `.env` 只参与变量插值和部分 provider/runtime 环境加载;它不是第二控制面。
shell 环境变量通常优先级更高。
