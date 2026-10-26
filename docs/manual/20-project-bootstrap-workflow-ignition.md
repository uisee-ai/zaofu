# 20 Project 创建、Bootstrap 与 Workflow 点火

> 适用对象：需要从空目录或已有代码库创建 ZaoFu Project，并通过 Kanban Agent、
> Channel、Research 或 CLI 安全点火第一条 Workflow 的操作者。
>
> 最后按 CLI、Web 与 Docker Playwright 验证：2026-07-29。

## 1. 先区分 Project、Request 和 Run

ZaoFu 将长期项目和一次执行分成三个生命周期：

| 对象 | 含义 | 是否长期存在 |
|---|---|---|
| Project | 项目根目录、唯一 `zf.yaml`、state dir、workspace 与集成配置 | 是 |
| Request | 一次需求的澄清、验收标准、kind 建议和点火申请 | 否，可有多条 |
| Run | 已批准 Request 的不可变执行快照 | 否，可有多轮 |

核心规则：

- `zf project init` 创建 Project，不等于启动 workflow。
- 默认 Project 是 multi-kind 容器，可依次接收 PRD、Issue、Feature 和 Refactor。
- Request 只有满足 readiness 且被显式批准后，才会产生
  `workflow.invoke.requested`。
- 一个项目保持一份 canonical `zf.yaml` 和一个 `project.state_dir`，不要为后续
  Issue 或 Feature 再创建第二套控制面。
- 首次安装 onboarding 与 Project init 分离：onboarding 可以在零 Project 时完成；
  Add/Open Project 只负责确定性生命周期动作。
- Kanban Agent 只在 Project 打开后接收 task prompt，再决定 Direct Coding、Task、
  Workflow、Channel 或 Research。

ZaoFu 源码仓库根目录的 `zf.yaml` 现在默认是标准 `PrdFlow`，用于本仓库自身的
PRD 交付。它不是新项目模板；新项目仍应通过 `zf project init` 或 Web
`Add/Open Project` 创建，默认行为仍是 multi-kind 且不点火。

## 2. 五个容易混淆的命令

| 命令 | 作用 | 是否启动 workflow |
|---|---|---|
| `zf profile bootstrap` | 探测技术栈，推荐/物化 Controller、checks 和指令文档 | 否 |
| `zf project init` | 创建 Project 容器、`zf.yaml`、state dir，并可注册 workspace | 默认否 |
| `zf init` | 为已有 `zf.yaml` 初始化或修复运行态 | 否 |
| `zf start` | 启动 worker、sidecar 和 watcher，等待入口事件 | 不会凭空创建 Request |
| `zf workflow routes/start` | 为已有 Task 查询 route、生成 proposal，并经授权 apply | `start --apply` 才会点火 |

类型化 Flow intake 的点火动作是 `zf flow submit --apply`，或者
`zf project init ... --apply` 这一显式 fast path。Kanban Agent、Channel member、
Coding Agent 和 CLI 的 Task-bound 统一入口则是 `zf workflow start`：
先 `--propose`，再由 operator 使用 exact proposal 和授权执行 `--apply`。

对于 light topology，`flow submit --apply` 会在同一个
`EventWriter` transaction 中追加受理事件和关联的 `prd.requested` / `issue.requested`
入口事件；不要再手工补发入口事件，否则会制造第二个 run identity。

## 3. CLI：创建一个默认 multi-kind Project

先设置 ZaoFu 源码和目标项目路径：

```bash
export ZAOFU_ROOT=/path/to/zaofu
export TARGET_PROJECT=/path/to/my-product
```

### 3.1 可选：先探测和审核 Bootstrap 建议

对已有代码库，先只读探测：

```bash
uv run --project "$ZAOFU_ROOT" zf profile bootstrap \
  "$TARGET_PROJECT" \
  --intent build \
  --backend claude-code \
  --scale launch
```

未初始化的新项目此时只做 Inspect，不要紧接着执行 `--apply`；下一节应使用
`project init` 物化默认 multi-kind Project。`profile bootstrap --apply` 是另一条配置
物化路径，适合明确选择 Bootstrap 推荐的单一 archetype 作为初始配置。不要对同一个
新项目无条件连续执行两种写入命令。

需要采用 Bootstrap 结果时，才显式执行：

```bash
uv run --project "$ZAOFU_ROOT" zf profile bootstrap \
  "$TARGET_PROJECT" \
  --intent build \
  --backend claude-code \
  --scale launch \
  --apply
```

空项目可增加 `--stack python|node|go|rust` 显式声明技术栈，并用 `--scaffold`
创建最小 `src/`、`tests/` 和 README。Bootstrap 不会启动 provider。multi-document
Flow 配置拥有自己的 gates；已有 multi-kind `zf.yaml` 不会被 Bootstrap Apply 自动
填入 `required_checks`，必须按下一节填写项目真实命令。

### 3.2 初始化 Project 容器

默认不传 `--kind`，创建 multi-kind Project：

```bash
uv run --project "$ZAOFU_ROOT" zf project init \
  --name my-product \
  --description "项目背景、长期目标与关键约束" \
  --root "$TARGET_PROJECT" \
  --create \
  --git-init \
  --backend codex \
  --verify-backend claude-code \
  --stack python \
  --workspace-register
```

该命令会：

- 创建或审核项目根目录；
- 生成唯一 `zf.yaml`；
- 生成项目专属 state dir 和 tmux/session 名；
- 物化 Issue、PRD、Refactor kind route；
- Issue 默认 1 lane，PRD 默认 2 lane，Refactor 默认 5 lane；
- 把 Project Brief 写入 `zf.yaml` 和 `AGENTS.md` Project Context；
- 根据声明或探测到的 Stack 生成 build/test Profile；
- 保留 primary backend，并把可选 verify backend 编译到独立验证 lane；
- 注册到 ZaoFu workspace；
- 不提交 Request，不产生 workflow invoke。

已有 Git 仓库不需要 `--git-init`。已有目录但不允许创建时去掉 `--create`。

### 3.3 审核生成结果

`project init` 生成的是 fail-closed 模板。点火前先替换所选 kind 文档中的 `TODO`
引用，并在最后一个 `ZfConfig.spec` 下配置目标项目可执行的机械门。例如：

```yaml
# PrdFlow.spec
prdRef: docs/intake/prd-account-security.md
targetRoot: app

# ZfConfig.spec
quality_gates:
  static:
    required_checks:
      - "cd app && npm run typecheck"
      - "cd app && npm test"
    on_fail: "candidate tree failed static gate; repair before reintegration"
workflow:
  rework_routing:
    static_gate.failed: prd-dev-lane-0
    test.failed: prd-dev-lane-0
```

命令必须与目标仓库当前脚本一致，route 也必须指向该 kind 实际存在的 impl owner；
多 lane 配置优先回同一 affinity lane。不要复制示例命令，也不要用
`workflow.allow_unverified_candidate` 绕过真实交付验收。

```bash
cd "$TARGET_PROJECT"

uv run --project "$ZAOFU_ROOT" zf validate --path zf.yaml
uv run --project "$ZAOFU_ROOT" zf validate --cold-start
uv run --project "$ZAOFU_ROOT" zf skills doctor
uv run --project "$ZAOFU_ROOT" zf workflow inspect
uv run --project "$ZAOFU_ROOT" zf start --dry-run --no-watch
```

`workflow inspect` 展示整个 multi-kind Controller 的静态图，可能同时列出未选 kind 的
诊断，或把仅由 runtime bridge 生产的事件标为静态缺 producer。点火裁决以当前 Request
的 `flow preflight --kind ...` 为准，但 `invalid_rework_target`、缺 role、缺 gate 等真实
`STOP` 仍必须修复，不能按 bridge 提示忽略。

在真实点火前检查：

- `project.name`、`project.state_dir` 和 tmux session 是否唯一；
- backend 是否已登录；
- `workflow.kind_routes` 是否包含预期 kind；
- `quality_gates.static.required_checks` 是否能在目标项目真实执行；
- `skill_sources`、workdir 和 Git base/target ref 是否正确；
- validation 是否仍有 placeholder、STOP 或缺失环境要求。

`flow preflight` 或 `flow submit --dry-run` 返回 `STOP` 时，不会产生 invoke 事件；先按
`fix-it` 补齐配置和产物，再重新预检。这是正常的 readiness 保护，不是启动失败。

## 4. CLI：澄清并点火第一条 PRD

### 4.1 创建 Request intake

```bash
mkdir -p docs/intake

uv run --project "$ZAOFU_ROOT" zf flow intake \
  --kind prd \
  --objective "实现账号安全设置页" \
  --target app \
  --acceptance "用户可以启用和关闭双因素认证" \
  --acceptance "相关单元测试和浏览器验收通过" \
  --request-id prd-account-security \
  --output docs/intake/prd-account-security.md
```

输入不完整时，Request 会停在 `clarifying`，不会创建执行任务。

### 4.2 补充信息并确认快照

```bash
uv run --project "$ZAOFU_ROOT" zf flow clarify \
  --config zf.yaml \
  --intake docs/intake/prd-account-security.md \
  --constraint "不得破坏现有登录会话" \
  --acceptance "失败场景有明确错误提示" \
  --confirm \
  --json
```

Readiness 至少要求：

- objective 非空；
- acceptance criteria 非空；
- open questions 已清零；
- kind 已解析；
- PRD 有 target root；Refactor 有 source root 和 target root；
- backend、profile、lanes 与环境 preflight 可用。

### 4.3 预检和只读预览

```bash
uv run --project "$ZAOFU_ROOT" zf flow preflight \
  --config zf.yaml \
  --kind prd \
  --intake docs/intake/prd-account-security.md \
  --json

uv run --project "$ZAOFU_ROOT" zf flow submit \
  --dry-run \
  --config zf.yaml \
  --intake docs/intake/prd-account-security.md \
  --kind prd \
  --json
```

`--allow-missing-env` 只适合受控 dry-run 或 CI 预览，不应拿来掩盖真实运行缺失的
provider、Git、tmux 或测试工具。

### 4.4 显式点火，自动启动 runtime

提交被接受后会自动在项目目录后台执行 `zf start`；已有运行器会复用。
无需先在另一个终端手动启动。需要前台观察时，仍可提前手动执行 `zf start`。

```bash
cd "$TARGET_PROJECT"
uv run --project "$ZAOFU_ROOT" zf flow submit \
  --apply \
  --config zf.yaml \
  --intake docs/intake/prd-account-security.md \
  --kind prd \
  --json
```

标准 kind route 已配置 pattern 时不需要手写 `--pattern-id`。自定义 route 没有默认
pattern 时，按 `zf flow submit --help` 显式提供。

点火后检查：

```bash
uv run --project "$ZAOFU_ROOT" zf events --last 30
uv run --project "$ZAOFU_ROOT" zf status --workers
uv run --project "$ZAOFU_ROOT" zf kanban --board
```

正常事件链包含 `workflow.submit.accepted` 和 `workflow.invoke.requested`。只有
runtime 消费 invoke 后，scan/plan/task map 和 Kanban task 才会继续出现。

## 5. 一条命令的明确需求 Fast Path

只有需求已经完整、验收标准明确且允许立即点火时，才把初始化和提交合并：

```bash
uv run --project "$ZAOFU_ROOT" zf project init \
  --name account-service \
  --root /path/to/account-service \
  --create \
  --git-init \
  --backend claude-code \
  --request-kind prd \
  --objective "交付账号安全设置页" \
  --target app \
  --acceptance "单元测试和浏览器验收通过" \
  --workspace-register \
  --apply \
  --json
```

即使提供了 `--apply`，missing fields 或 open questions 仍会 fail closed，Request
停在 `clarifying`，不会带病点火。

## 6. 何时使用单 kind Project

兼容入口仍可显式创建单 kind Controller：

```bash
zf project init --kind issue ...
zf project init --kind prd ...
zf project init --kind refactor ...
```

适合一次性、边界固定且确认不会继续承载其他类型需求的项目。长期产品建议保留
默认 multi-kind；后续 Feature 内部按 light PRD route 处理，Issue 默认单 lane。

## 7. Web：全局 Onboarding 与 Add/Open Project

以默认可写的 workspace 模式启动 Dashboard：

```bash
"$ZAOFU_ROOT/tools/start-webkanban.sh" \
  --host 127.0.0.1 \
  --port 8001 \
  --workspace-only
```

launcher 会复用或创建 action token，输出 `write_url`。用该链接打开页面即可自动
获得操作权限，无需手动粘贴 Token；`--host 0.0.0.0` 时将链接中的主机换成服务器 IP。
launcher 统一加载 Workspace/provider 环境和可信本地 Codex headless sandbox 策略。
显式传入 `--read-only` 可禁用写操作。直接 `zf web` 在 loopback 且未配置权限策略时
也默认可写；网络监听使用授权链接，已有 Token/Passcode 配置继续生效。

首次安装引导固定为：

1. Provider：选择 Codex 或 Claude Code 作为 primary provider；两者都可用时可启用
   Mixed team，由另一 provider 承担独立 verify lane。
2. Environment：检查宿主依赖。
3. Access：授权当前浏览器的 Web action session/token。
4. Ready：完成并进入 Workspace。

此流程不要求创建第一个 Project。空 Workspace 是正常状态。

加入 Project 时点击 `Add Project`：

1. 输入服务端上的 Project path。
2. 点击 `Inspect`。
3. 审核后端给出的唯一动作和 diagnostics。
4. 当动作为 `initialize_project` 时，确认 Project Name、可选 Project Brief、
   自动探测或声明的 Project Stack、Primary Provider 与 Mixed team。其他动作不会
   显示或改写这些字段。
5. 执行 `Open Project`、`Add & Open`、`Initialize & Open` 或
   `Create Project`。

![Add/Open Project 当前创建表单](assets/project-add-open-current.png)

![Add/Open Project 动态演示](assets/quickstart-project.webp)

对话框不再要求选择 Existing/Create、YAML、preset、controller、kind、scale、lane
或 role，也不接收初始 task prompt。Stack 只用于确认项目语言与命令，不决定 workflow。
已有合法配置按其
`project.state_dir` 判定并保持不变；无配置目录内部生成默认 multi-kind Project；
无效配置或残缺非空 state 会显示 `blocked`，不会调用 init/register。

Project Brief 是长期项目元数据，保存在 `zf.yaml` 的
`project.description`，显示在 Project Overview，并写入 `AGENTS.md` 的 Project
Context 托管段。技术栈及 build/test/gate 命令写入独立 Profile 托管段。`CLAUDE.md`
只引用 `AGENTS.md` 并保留 Claude 专属规则，不复制项目上下文；初始化也不会创建
Task。Task Prompt 仍在 Project 打开后输入 Kanban Agent。Mixed team 不是
`backend: mixed`：生成配置保留 primary backend，并把另一 provider 编译到 verify
lane 的 backend。

Web greenfield、CLI `project init` 与 `tools/init-project.sh` 的 Project 容器语义
一致：三者复用 `init_flow_project`；Web 对不存在/空目录生成 seed 与 Git HEAD，
脚本还负责已有目录的交互式 Git readiness、validate 与 startup dry-run。它们创建并
注册 Project，但不自动创建 Task、workflow intake 或 workflow invoke。打开 Project
后，可以从 Kanban Agent chat、Channel 或 CLI 讨论需求；只有
明确创建/确认 Task 并完成 Workflow Plan/Approve 后，Task-bound Workflow 才点火。

## 8. Kanban Agent、Channel 与 Research 的受控点火

### 8.1 先选需求入口，不在创建 Project 时选 Workflow

| 入口 | 是否需要已有 Task | 是否直接点火 |
|---|---:|---:|
| Kanban Agent 普通 Coding | 否 | 否，按普通 provider session 工作 |
| `Create Task` | 否 | 否，只创建可追踪 Task |
| Channel setup/discussion | 否 | 否，只创建协作空间；默认 conversation 等待定向交互 |
| Research Workflow | 是 | Plan 后还需 Approve |
| PRD/Issue/Refactor/Planning Workflow | 是 | Plan 后还需 Approve |

Kanban Agent 在 Project 打开后基于具体需求判断业务类型、复杂度和验收目标。它只能
推荐当前 `zf.yaml` route catalog 中 active 的 route，不得从聊天文本发明 topology、
pattern、lane 或 role。

### 8.2 Channel 只形成协作产物

Channel setup Plan 选择后可直接执行 `channel-create-and-start`，一次创建 Channel、
模板 Members、投递原始需求并初始化 discussion mode：

![Channel setup Plan](assets/kanban-channel-plan.png)

这是 Plan direct-apply 的受控例外，不代表 Channel 可以点火 Workflow。默认
`conversation` 不自动 fanout；只有显式 Discuss / `multi_lens` 才运行有界多视角讨论。
显式 Finalize 产生 PRD draft，只有 Owner confirm 的 revision 才是 canonical PRD。
任何 synthesis 或 confirmed PRD 都不会自动创建 Task。人需要明确要求并确认
`Create Task` proposal；PRD 拆分、planning artifact 和 `task_map` 在后续 Workflow
planning 阶段生成。

### 8.3 Task-bound Workflow 是 Plan 与 Approve 两步

已有 Task 后，所有 surface 使用同一服务：

```text
zf workflow routes --task TASK-ID
-> semantic planner 推荐 active route
-> Plan 选择 route / 参数
-> workflow start proposal
-> 独立 Approve exact proposal
-> workflow.invoke.requested
```

![Task-bound Workflow Plan](assets/kanban-task-workflow-plan.png)

![Workflow exact proposal Approve](assets/kanban-task-workflow-approve.png)

![Task 创建、Workflow Plan、独立批准与点火动态演示](assets/quickstart-direct-workflow.webp)

Plan 允许 `Chat about` 和 `Customize`，用于补齐 source/input refs、expected output、
scope 或会改变 route 的参数。选择 route 只生成 proposal，不等于已经运行。

CLI 使用相同的 surface-neutral 服务：

```bash
zf workflow routes --task TASK-ID --format json

zf workflow start \
  --task TASK-ID \
  --route research:fixed \
  --objective "调研账号恢复方案并形成证据化建议" \
  --parameters-json '{"expected_output":"research synthesis plus PRD inputs"}' \
  --propose \
  --format json
```

只有 operator 持有授权时才 apply exact proposal：

```bash
zf workflow start \
  --proposal-event-id EVENT-ID \
  --authorization-ref APPROVAL-REF \
  --authorization-token "$ZF_WORKFLOW_ACTION_TOKEN" \
  --apply \
  --format json
```

Provider/Coding Agent 不应接收或读取 `ZF_WORKFLOW_ACTION_TOKEN`。

### 8.4 Research 是一种注册 Workflow route

固定 Research route 为 `research:fixed`，只有当前 Project catalog 提供且可用时才可
选择。它需要 Task，固定角色为 `source_researcher`、`product_analyst`、
`technical_analyst`、`risk_critic` 和 `synthesizer`，输出 summary、evidence refs、
open questions 与 PRD/Refactor prompt inputs。

`research-review` Channel 模板只是讨论，不会隐式启动 Research Workflow。Research
完成后也由人决定是否创建/更新交付 Task，不自动点火 PRD Workflow。

## 9. 常见问题

### Initialize 后为什么没有 task？

正常。Initialize 只创建 Project。可以直接进行普通 Coding；需要受控 Workflow 时，
先创建/确认 Task，再完成 Workflow Plan 与 Approve，最后确认
`workflow.invoke.requested` 被运行中的 watcher 消费。

### Channel 已经输出 PRD，为什么没有 Task？

这是预期边界。Channel 输出是协作产物，不能自动承诺执行。明确要求 Kanban Agent
生成 `Create Task` proposal，确认后再为该 Task 选择 Workflow。

### `zf start` 后为什么所有 pane 都 idle？

`zf start` 只启动 runtime。没有已接受的入口事件时，worker 等待是正确行为。

### `flow submit --apply` 为什么被拒绝？

检查 intake 的 objective、acceptance、open questions、kind roots 和 preflight。
不要手工伪造 invoke 事件绕过 readiness。

### Dashboard 显示 Project needs initialization？

确认 workspace registry 中的 `root`、`config_path` 和 `state_dir_hint` 指向同一
Project，并从项目根运行 `zf validate --cold-start`。在 Add/Open Project 中重新输入
root 并执行 Inspect；按返回的 `register`、`initialize_state`、
`initialize_project` 或 `blocked` 处理，不要手工猜测 Existing/Create。

### 根 `zf.yaml` 是 PRD，为什么新项目却是 multi？

根配置是 ZaoFu 自身的默认工作流；`project init` 是产品级项目容器入口。两者用途
不同，不应通过复制根配置创建外部项目。

## 10. 完成检查表

- Project 只有一份 canonical `zf.yaml`。
- `project.name`、`project.description` 与 provider policy 符合预期；mixed 配置中
  不存在 `backend: mixed`。
- `AGENTS.md` 的 Project Context/Profile 与 Project Brief、Stack 和真实命令一致。
- `project.state_dir`、tmux session、branch prefix 和端口不会与其他项目冲突。
- workspace 已注册，Dashboard 能正确切换 Project。
- Bootstrap 推荐已人工审核，quality checks 在目标项目可执行。
- Channel 结论如需交付，已经由人确认成真实 Task；没有隐式自动建 Task。
- Task-bound route 来自当前 catalog，proposal 绑定 exact Task/config digest。
- Request/Task 有 objective、acceptance、正确 refs/roots，且没有 open questions。
- submit/proposal 预览无 STOP，显式批准后才 apply。
- `zf start` 的 watcher 保持运行，事件、Kanban 和 worker 状态可观测。
- 停止时只执行当前项目的 `zf stop`，不要使用 `tmux kill-server`。
