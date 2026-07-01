# `zf.yaml` 控制面与运行态

> 适用对象: 需要配置角色、技能、workdir、质量门禁、预算和恢复策略的操作者。

## 1. 基本原则

`zf.yaml` 是 ZaoFu 的唯一控制面。角色、事件、质量门禁、技能源、运行态目录、预算、安全策略都应该通过它声明。

不要引入第二套任务 schema 或外部控制面。外部系统只能通过事件、CLI 或明确的 kernel API 写入意图,不能直接改业务真相。

最小形态:

```yaml
version: "1.0"
preset: safe-team
project:
  name: my-project
  state_dir: .zf
session:
  tmux_session: zf
orchestrator:
  backend: claude-code
roles:
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
| `orchestrator` | L2 orchestrator backend、turn、timeout、冷却配置 |
| `roles` | worker/orchestrator 角色列表 |
| `skill_sources` | skills 来源目录 |
| `runtime` | workdir、git isolation、skills materialize 配置 |
| `workflow` | rework routing、wake extension、fanout stage 等流程配置 |
| `quality_gates` | 外部命令门禁 |
| `verification` | contract/scope/architecture/promoted 等 discriminator 开关 |
| `autopilot` | deterministic proposal-only 自检 |
| `security` | event signing 等安全配置 |
| `global_budget_usd` | 全局预算硬上限 |

判断一个字段是否真实生效时,以 `src/zf/core/config/schema.py` 和 runtime 引用为准。设计文档中仍保留了一些未落地意图。

## 3. Role 配置

角色的 `name` 是 role type,`instance_id` 是展开后的 worker 实例 ID。`replicas > 1` 时,例如 `name: dev, replicas: 4`,会展开为 `dev-1` 到 `dev-4`。

常用字段:

| 字段 | 作用 |
|---|---|
| `name` | 角色类型,如 `orchestrator`、`arch`、`dev`、`review`、`test`、`judge` |
| `backend` | `claude-code`、`codex`、`python` 等 |
| `backends` | 多副本时按副本指定 backend,长度必须等于 `replicas` |
| `model` | 留空表示使用 provider CLI 默认模型 |
| `permission_mode` | `bypass` 或 `allowlist` |
| `allowed_tools` | allowlist 模式下的工具白名单 |
| `transport` | 默认 `tmux`;旧 `stream-json` 仅保留兼容 |
| `replicas` | 静态副本数 |
| `role_kind` | `auto`、`writer`、`reader`;配合 workdir/git isolation |
| `skills` | 当前角色启用的 skill 名 |
| `plugins` / `agent` | Claude-only 扩展;Codex 会忽略或只消费注入文本 |
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
- 多副本 dev/test 应优先显式设置 `replicas`,不要复制多个同名 role。

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
| orchestrator | `auto` | 调度者不应直接写代码 |

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

## 7. 运行态真相文件

运行态目录由 `project.state_dir` 决定。默认 `.zf/` 中的关键文件:

| 文件 | 说明 |
|---|---|
| `events.jsonl` | append-only 事件日志,核心 truth |
| `kanban.json` | task truth |
| `feature_list.json` | feature truth |
| `session.yaml` | harness session 状态 |
| `role_sessions.yaml` | role instance 到 provider session 的映射 |
| `cost.jsonl` | 成本投影 |
| `skills.lock.json` | skill 解析/物化投影 |
| `instructions/` | 生成给各 role 的指令 |
| `workdirs/` | runtime workdir、skills manifest、隔离 checkout |
| `runs/` | E2E/真实 run archive |

不要手写 `events.jsonl`、`kanban.json`、`feature_list.json`、`session.yaml`、`role_sessions.yaml`。代码层应使用 `EventWriter`、`TaskStore`、`FeatureStore`、`SessionStore`。

## 8. 兼容性提示

大部分新 CLI 会解析 `project.state_dir` 或提供 `--state-dir`。少数 legacy 命令当前仍默认读取当前目录下 `.zf/`,例如部分 `watch`、`feature`、`cost`、`stop` 路径。使用自定义 state dir 时,优先选择支持 `--state-dir` 的命令,或从项目根目录和默认 `.zf` 布局运行。
