# 20 Project 创建、Bootstrap 与 Workflow 点火

> 适用对象：需要从空目录或已有代码库创建 ZaoFu Project，并安全提交第一条
> PRD、Issue 或 Refactor workflow 的操作者。
>
> 最后按 CLI 与 Web 验证：2026-07-17。

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

ZaoFu 源码仓库根目录的 `zf.yaml` 现在默认是标准 `PrdFlow`，用于本仓库自身的
PRD 交付。它不是新项目模板；新项目仍应通过 `zf project init` 或 Web wizard
创建，默认行为仍是 multi-kind 且不点火。

## 2. 四个容易混淆的命令

| 命令 | 作用 | 是否启动 workflow |
|---|---|---|
| `zf profile bootstrap` | 探测技术栈，推荐/物化 Controller、checks 和指令文档 | 否 |
| `zf project init` | 创建 Project 容器、`zf.yaml`、state dir，并可注册 workspace | 默认否 |
| `zf init` | 为已有 `zf.yaml` 初始化或修复运行态 | 否 |
| `zf start` | 启动 worker、sidecar 和 watcher，等待入口事件 | 不会凭空创建 Request |

真正的点火动作是 `zf flow submit --apply`，或者 `zf project init ... --apply`
这一显式 fast path。

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
  --backend claude \
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
  --backend claude \
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
  --root "$TARGET_PROJECT" \
  --create \
  --git-init \
  --backend claude \
  --workspace-register
```

该命令会：

- 创建或审核项目根目录；
- 生成唯一 `zf.yaml`；
- 生成项目专属 state dir 和 tmux/session 名；
- 物化 Issue、PRD、Refactor kind route；
- Issue 默认 1 lane，PRD 默认 2 lane，Refactor 默认 5 lane；
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

### 4.4 启动 runtime，再显式点火

终端 A：

```bash
cd "$TARGET_PROJECT"
uv run --project "$ZAOFU_ROOT" zf start
```

终端 B：

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
  --backend claude \
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

## 7. Web：创建 Project 与 Bootstrap Inspect

设置受控写操作 token，并以 workspace 模式启动 Dashboard：

```bash
export ZF_WEB_ACTION_TOKEN="$(openssl rand -hex 24)"
uv run --project "$ZAOFU_ROOT" zf web \
  --host 127.0.0.1 \
  --port 8001 \
  --workspace-only
```

首次引导按以下顺序操作：

1. 选择 provider backend。
2. 完成环境自检。
3. 输入目标项目目录并执行 Bootstrap Inspect。
4. 审核推荐的 Controller、setup、quality checks 和指令文档。
5. 打开 Add Project，选择 Create。
6. 长期项目选择 `kind: multi`；选择 backend、stack、scale 和 intent。
7. 按需勾选 profile overlay 与 scaffold，先 Validate，再 Initialize。

![Bootstrap Inspect 会展示 Controller、setup、gate 与指令文档候选](assets/project-bootstrap-inspect.png)

![New Project 默认选择 multi-kind，初始化与 workflow 点火分离](assets/project-create-multi-kind.png)

Web Initialize 与 CLI `project init` 语义一致：创建并注册 Project，但不自动点火。
后续需求从 Kanban Agent、Channel 或 CLI 进入同一 Request service。

## 8. Kanban Agent 与 Channel 的受控点火

- Kanban Agent 可以澄清 objective、验收标准和 kind/tier/lane 建议。
- Channel 共识可以让 Request 进入 ready/proposed。
- 两者都不能直接写 `kanban.json`、`session.yaml` 或直接 invoke workflow。
- 最终点火必须经过 Web token action、owner approval card 或 CLI `--apply`。
- 同一个 `request_id` 的 revision 与 requirement digest 应连续可追溯。

## 9. 常见问题

### Initialize 后为什么没有 task？

正常。Initialize 只创建 Project。先生成并批准 Request，再确认
`workflow.invoke.requested` 已被运行中的 watcher 消费。

### `zf start` 后为什么所有 pane 都 idle？

`zf start` 只启动 runtime。没有已接受的入口事件时，worker 等待是正确行为。

### `flow submit --apply` 为什么被拒绝？

检查 intake 的 objective、acceptance、open questions、kind roots 和 preflight。
不要手工伪造 invoke 事件绕过 readiness。

### Dashboard 显示 Project needs initialization？

确认 workspace registry 中的 `root`、`config_path` 和 `state_dir_hint` 指向同一
Project，并从项目根运行 `zf validate --cold-start`。已有目录应先 Bootstrap Inspect，
再选择 Existing Register 或 Create Initialize。

### 根 `zf.yaml` 是 PRD，为什么新项目却是 multi？

根配置是 ZaoFu 自身的默认工作流；`project init` 是产品级项目容器入口。两者用途
不同，不应通过复制根配置创建外部项目。

## 10. 完成检查表

- Project 只有一份 canonical `zf.yaml`。
- `project.state_dir`、tmux session、branch prefix 和端口不会与其他项目冲突。
- workspace 已注册，Dashboard 能正确切换 Project。
- Bootstrap 推荐已人工审核，quality checks 在目标项目可执行。
- Request 有 objective、acceptance、正确 roots，且没有 open questions。
- submit dry-run 无 STOP，显式批准后才 apply。
- `zf start` 的 watcher 保持运行，事件、Kanban 和 worker 状态可观测。
- 停止时只执行当前项目的 `zf stop`，不要使用 `tmux kill-server`。
