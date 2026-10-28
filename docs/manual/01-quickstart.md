# ZaoFu 快速开始

> 适用对象：第一次安装 ZaoFu，并希望从 Web 完成 Bootstrap、创建或打开
> Project，再通过 Kanban Agent 使用 Channel、Research 和交付 Workflow 的操作者。
>
> 核心 CLI、Web、事件账本和 Workflow 路线最后整体核实于 2026-08-03；Web Terminal 的安装、
> 鉴权、多 Tab 与跨客户端恢复切片核实于 2026-08-21。
> 本页动态图和关键截图由 Playwright 的真实交互状态组成；闭环结论同时检查对应 API、
> Store 和 EventLog，不以截图代替运行态证据。
> Channel 当前以 `conversation` 为默认产品模式；只有显式选择 `multi_lens` 并执行
> Discuss 才进入有界 fanout/synthesis。

## 完成路线

```text
安装 ZaoFu
  -> Bootstrap（安装级设置）
  -> Add/Open Project（项目级初始化）
  -> Kanban Agent
       |-> 直接 Coding
       |-> 创建 Channel -> 多角色讨论 -> 人工确认 Create Task proposal
       |-> 创建 Research Task -> Plan -> Approve -> Research Workflow
       `-> 创建普通 Task -> Plan -> Approve -> Delivery Workflow
```

三条边界先记住：

- Bootstrap 不创建 Project；Project 初始化不创建 Task，也不启动 Workflow。
- Channel 独立于 Workflow；讨论结论不会自动创建 Task 或自动点火。
- Research 和交付 Workflow 共用 Task-bound 启动链路，但选择不同 active route。

## 1. 安装 ZaoFu（必需）

必需环境：

- Python 3.11+、`uv`、Git、`tmux`；
- 至少一个已安装并登录的 provider CLI：Codex 或 Claude Code；
- 使用 Claude Code stream-json transport 时安装 `stream-json` extra。

从 source checkout 安装并检查 CLI：

```bash
git clone https://github.com/uisee-ai/zaofu /path/to/zaofu
cd /path/to/zaofu
uv sync --extra dev --extra web --extra stream-json

uv run zf --version
uv run zf doctor provider --backend codex
```

启动 Workspace Dashboard：

```bash
export ZAOFU_ROOT=/path/to/zaofu
"$ZAOFU_ROOT/tools/start-webkanban.sh" \
  --host 127.0.0.1 \
  --port 8001 \
  --workspace-only
```

该 launcher 会构建 Web、复用或创建 Web action token、加载 Workspace provider
环境，并为可信本地 Codex headless 应用兼容的 sandbox 策略。浏览器访问
`http://127.0.0.1:8001/`。只有在可信网络中才绑定 `0.0.0.0`。

完成标志：Dashboard 打开并进入安装 Onboarding。安装步骤只需要按本教程执行，
本页不要求先创建示例 Project。

### Web Terminal（默认启用，可选安装）

Dashboard host 默认启用 Web Terminal；这只开放受鉴权的 Terminal 能力，不会自动安装或
启动 CLI。若要在 Dashboard 内实际运行 Claude Code/Codex TUI，还需安装 Herdr `>=0.8.0`；
它是宿主二进制，不属于 `uv sync` 的 Python 依赖：

```bash
curl -fsSL https://herdr.dev/install.sh | sh
herdr --version
herdr api schema --json >/dev/null
herdr terminal session observe --help
herdr terminal session control --help
```

不需要给 v4 或其他目标 Project 的 `zf.yaml` 增加 `runtime.web_terminal`。若这个 Dashboard
host 不允许 Web Terminal，可在启动它的 ZaoFu 配置中显式关闭：

```yaml
runtime:
  web_terminal:
    enabled: false
```

不要配置 terminal-only Provider 名单。New Session 会读取所选 Project 初始化后生效的
orchestrator/roles backend：single Project 只显示一个 Provider，Mixed team 显示 Claude Code
与 Codex。安装、鉴权、三种连接模式和生产检查见 [Web Terminal 专题](24-web-terminal.md)；
可先观看[跨终端与离线恢复中文演示](showcases/web-terminal.md)。

## 2. Bootstrap（必需）

首次进入按四步完成安装级设置：

1. **Provider**：选择 Codex 或 Claude Code 作为 primary provider；两者都可用时可启用
   Mixed team，由另一个 provider 承担独立 verify lane。
2. **Environment**：检查宿主依赖和 provider 可用性。
3. **Access**：为当前浏览器建立受控 action session。
4. **Ready**：进入空 Workspace。

![Bootstrap 四步动态演示](assets/quickstart-bootstrap.webp)

Bootstrap 只写 Workspace/onboarding 设置，不调用 Project init。零 Project 的空
Workspace 是正确结果。

完成标志：Ready 页显示 Provider、Team、Environment 和 Access 均可用。

## 3. New/Open Project（必需）

进入 Workspace 后，点击左侧 Project 选择器旁的 `+`：

1. 输入**服务端本机**上的 Project path。
2. 点击 `Inspect`。
3. 审核服务端返回的唯一 admission action 与 diagnostics。
4. 仅在 `initialize_project` 时填写 Project Name、Project Brief、Project Stack、
   Primary Provider 和可选 Mixed team。
5. 执行界面给出的 `Open Project`、`Add & Open`、`Initialize & Open` 或
   `Create Project`。

![Add/Open Project 动态演示](assets/quickstart-project.webp)

Inspect 按磁盘真相选择动作：

| 目录状态 | 行为 |
|---|---|
| 已注册且健康 | 直接打开 |
| 有合法 `zf.yaml`、未注册 | 注册并打开 |
| 有合法配置、缺运行态 | 初始化 state 后打开 |
| 没有 `zf.yaml` | 创建默认 multi-kind Project |
| 配置无效或存在残缺非空 state | `blocked`，先修复，不猜测覆盖 |

Project Brief 应写长期背景、目标和关键约束，不要写单次 Task Prompt。初始化后：

- `project.description` 保存到 `zf.yaml`；
- Project Context 托管段写入 `AGENTS.md`；
- Stack 及探测到的 build/test 命令写入独立 Profile 托管段；
- `CLAUDE.md` 保持 Claude 专属规则，并引用 `AGENTS.md`；
- Project 注册到 Workspace，但不会自动创建 Task 或启动 Workflow。

目标路径不存在或为空时，`Create Project` 会生成最小 README/src/tests、独立 Git
repository 和 initial HEAD，使默认 worktree runtime 可以启动。已有非空代码目录不会
被 Web 自动 `git init` 或提交，需要 operator 先建立可信 Git baseline。

已有注册 Project 不需要重新导入；刷新 Workspace 后直接打开即可。

### `zf.yaml` 的位置

`zf.yaml` 仍是唯一控制面。Add/Open Project 不再让用户选择 YAML、preset、
Controller、kind、scale、lane 或 role：

- 已有合法 `zf.yaml` 原样保留；
- 新目录生成默认 multi-kind `zf.yaml`；
- Stack 只决定项目指令和命令 Profile，不决定 Workflow；
- Provider 选择编译 provider policy；Mixed team 不产生 `backend: mixed`；
- Kanban Agent 只能从当前 `zf.yaml` 展开的 active route catalog 推荐 Workflow。

显式选择单一 Controller、迁移控制面或物化 Bootstrap 推荐时，才使用
`zf profile bootstrap`。

完成标志：Project Overview 显示正确名称和 Brief，Project 出现在 Workspace
选择器中。

## 4. 使用 Kanban Agent（必需）

Kanban Agent 是 Project 内的通用 Coding Agent，不只是创建 Task、查看状态或监工。
它可以在当前 provider session 内分析和修改 Project 代码、运行测试；是否建立 Task
取决于你是否需要可追踪执行。

| 目标 | 是否先要 Task | 交互与结果 |
|---|---:|---|
| 普通分析、修改代码、运行测试 | 否 | 直接 Coding，受当前权限和 Git 规则约束 |
| 只建立可追踪工作项 | 否 | `Create Task` proposal，确认后创建 Task |
| 多角色澄清、评审或讨论 | 否 | Channel setup Plan；创建后默认进入自然 conversation，多视角讨论需显式启动 |
| 固定角色深度研究 | 是 | 对已有 Task 给出 Research route Plan，随后独立 Approve |
| PRD/Issue/Refactor/Planning 交付 | 是 | 对已有 Task 推荐 active route，随后独立 Approve |

交互只有两个需要人工停顿的核心形态：

- **Plan**：澄清路线、模板、成员、轮次和参数；`Chat about` 可继续讨论或自定义。
- **Approve**：确认 exact action、Task、route、objective 和参数后才执行副作用。

不要在创建 Project 时预先决定 lane 数或角色。Kanban Agent 应基于具体需求的业务类型、
复杂度和验收目标，从 active catalog 推荐单 lane、多 lane、Research 或其他已注册
route。

使用 Codex 时，Kanban Agent 从本机 Provider 查询模型目录。输入框发送键旁的菜单可选择
模型和思考强度；选择按 Project、会话和 Provider 保存，后续消息沿用，直到用户重置。
排队消息保留提交时的选择，历史记录将其标记为请求值，不冒称已确认的实际值。未选择
模型时可单独调整所有目录模型共有的思考强度，不更换当前会话模型。服务端在首次覆盖
前保存原有模型和 effort；用户重置后在下一轮恢复。Provider 无法提供可恢复配置时拒绝覆盖。目录加载
失败时可重试；无法确认模型能力的 Provider 使用其既有配置。
没有手动选择时，菜单展示 Codex 返回的项目配置或会话基线；无法确认的字段显示
Default / Auto，不把模型目录的推荐值当成当前生效值。
若菜单提示 Dashboard 认证被拒绝，选择重新输入 action token；若提示模型目录不可用，
可重试目录加载。
输入框左侧“＋”菜单汇集文件、上下文、工具与 Agent profile。菜单中的 ZaoFu 主机策略
目录不代表 Provider 原生工具已被拦截或可用；实际权限和
工具结果以 Provider 的权限和回执为准。没有明确结果状态时，界面显示结果状态未知。
输入区的“添加”菜单可引用当前会话中已有的 Plan；草稿、附件和 Plan 引用按 Project
及会话保存，切换后可回到原会话继续编辑。上传完成后才能发送，受控动作仍按现有配置执行。

两个聊天界面通过顶栏区分：

| 界面 | 顶栏标识 | 用途 |
|---|---|---|
| Kanban Agent | `Kanban Agent`、provider 和 `active` 状态 | 与 Project 通用 Coding Agent 对话并处理 Plan/Approve |
| Channel Group | `# Channel name`、Channel ID 和成员图标计数 | 在已创建 Channel 中进行多角色讨论 |

即使 Kanban Agent 处于全屏，左上角仍会显示 `Kanban Agent`；Channel 页面始终以
`#` 和 Channel 名称开头。

## 5. 用 Kanban Agent 创建 Channel（推荐）

产品里常说的 Channel Group，canonical 模型是运行时的 **Channel + Members**，不是
`zf.yaml` 中的静态配置块。向 Kanban Agent 明确要求多角色讨论，例如：

```text
为 API authentication 变更创建一个聚焦的评审 Channel。
使用自然 conversation，不要自动 fanout、创建 Task 或启动 Workflow。
```

Kanban Agent 返回 Channel setup Plan。选项显示模板、成员角色、成员数和 discussion mode；
`Chat about` 允许先调整范围，选择完成后点击 `Create & start`。

![Kanban Agent 内的 Channel setup Plan](assets/quickstart-kanban-channel-plan.png)

上图中推荐方案的完整配置是：

```text
Quick Change
  members: 3
  roles: tech_leader, dev_reviewer, qa_analyst
  max_rounds: 4
```

界面显示的 `4 rounds` 对应 `overrides.budget.max_rounds`。它只在显式启动
`multi_lens` 等有界多视角讨论时限制轮次；不会让默认 `conversation` 自动 fanout，
也不是 Kanban Agent provider 的 `max_turns`。需要改变角色、成员数、mode 或预算时，
先点击 `Chat about`，让 Kanban Agent 返回修订后的 Plan。

系统一次完成：

```text
创建 Channel
-> 物化模板成员、角色上下文、技能与写权限
-> 把原始需求发到 Channel
-> 初始化模板声明的产品模式
-> conversation 等待人/Leader 定向交互
-> 只有显式 Discuss / multi_lens 才执行有界多视角讨论
```

不需要再手工创建 Channel、邀请成员或复制第一条消息。创建 Channel 不会产生
`workflow.invoke.requested`。

点击后，Kanban Agent 先折叠 Plan，并保留最终模板、角色、成员数和轮次：

![Channel 创建完成后的 Plan applied](assets/quickstart-channel-applied.png)

当前 Web 不会在 `Plan applied` 后自动把主页面切到 Channel。进入新 Channel 的步骤是：

1. 点击 Kanban Agent 右上角的 `Minimize Kanban Agent`（`-`）。
2. 在左侧 `Channels` 区域等待新 Channel 出现；行尾数字是成员数。
3. 点击 Channel 名称。
4. 看到 `# Channel name` 后，点击右上角 Members 图标核对成员。

```text
[Kanban Agent]  Plan applied
        |
        | Minimize
        v
左侧 Channels -> API authentication review                         3
        |
        v
[# API authentication review]  Chat | Details              Members 3
```

![从左侧 Channels 进入并核对 3 名成员](assets/quickstart-channel-members.png)

完成标志：Plan 显示 `Plan applied`；左侧新 Channel 行显示预期成员数；打开后原始需求、
成员角色和讨论状态均可见。

## 6. 在 Channel Group 内讨论（推荐）

Channel 以 thread 保存原始需求、角色回复、开放问题和收敛结论。产品只暴露
`conversation`、`clarification` 和 `multi_lens` 三种模式；底层
`manual_mention`、`mention_relay`、`fanout_then_synthesis` 是兼容 engine 映射。
角色权限、技能、Leader 和默认 responder 来自模板物化结果。

![Channel Group 多角色讨论与继续输入](assets/quickstart-channel-discussion.webp)

Channel 顶栏 Members 图标旁的数字来自当前 Channel 的 canonical `members`，不是
Project 全部 Agent 数，也不是后续 Workflow roles 数。点击图标可查看每个成员的
角色、状态、provider 和写权限；上例应为 3 名成员：
`tech_leader`、`dev_reviewer`、`qa_analyst`。

讨论结束后：

- 人可以在同一 Channel 继续输入新问题，或延续上一个需求；
- 显式 Finalize 可以形成 PRD draft，只有 Owner confirm 的 revision 才是 canonical PRD；
- Channel 和 Kanban Agent 都可以基于结论提出 `Create Task` proposal；
- **Task proposal 必须由人确认，不能由 Channel 自动创建**；
- PRD 拆分属于后续 Workflow planning，不由 Channel 或 Kanban Agent 直接改写
  canonical Task。

启用飞书时，同一 Channel、消息、审批意图和结果通过事件/受控 action 投影到飞书，
不建立第二套业务状态。完整模板和飞书用法见
[15 Channel 协作使用手册](15-channel-collaboration.md)。

需要回到通用 Coding 对话时，点击页面右下角的 `Open Kanban Agent`；Channel thread
不会因此关闭或丢失。

完成标志：角色回复和 synthesis 可见，composer 仍可继续输入；Task 和 Workflow
不会因为讨论结束而自动出现。

## 7. 创建 Research Workflow（按需）

Research 是 Task-bound Workflow，不是 Channel 模板的别名。先创建或选中一个 tracked
Task，再让 Kanban Agent 推荐 Research route：

```text
为 API authentication research 创建 Task，然后推荐固定角色 Research Workflow。
```

![Research Task、Plan、Approve 与点火](assets/quickstart-research.webp)

启动链路为：

```text
已有 Task
-> 读取 active route catalog
-> Plan 选择 research:fixed 和参数
-> 独立 Approve
-> workflow.invoke.requested
-> research-fanout
```

默认固定角色为 `source_researcher`、`product_analyst`、
`technical_analyst`、`risk_critic` 和 `synthesizer`。Research 产出研究摘要、
证据引用、开放问题和 PRD/Refactor prompt inputs；不会自动创建交付 Task。

`research-review` Channel 模板只负责讨论/评审。只有已有 Task、用户明确选择
Research，且 Project active catalog 提供 `research:fixed` 时，才启动 Research
Workflow。

完成标志：Approve 卡显示 exact Task 和 `research:fixed`，确认后显示
`Workflow started`，账本中只有一条绑定该 Task 的 `workflow.invoke.requested`。

## 8. 直接创建 Task 并启动交付 Workflow（推荐）

需要把明确需求交给已注册交付 route 时，可以在一次 Kanban Agent 对话中要求：

```text
为 authentication policy validation 创建 Task，并在启动前推荐一个聚焦交付 Workflow。
```

![直接创建 Task 并启动交付 Workflow](assets/quickstart-direct-workflow.webp)

闭环顺序固定：

```text
Create Task proposal
-> 人确认创建 Task
-> Workflow Plan（active route / topology / output / 参数）
-> 人选择或 Chat about / Customize
-> 独立 Approve
-> workflow.invoke.requested
```

Plan 不是授权。点击 `Continue` 只把选择固化为 exact proposal；只有
`Start workflow` 才执行点火。简单任务也必须映射到 `zf.yaml` 中已注册的 stage，
不能发明一个无法投影到 Kanban board 的“单 agent lane”。

CLI 可检查同一套 surface-neutral route：

```bash
cd /path/to/project
uv run --project "$ZAOFU_ROOT" zf workflow routes \
  --task TASK-ID \
  --format json
```

完成标志：Task 已创建，Approve 后出现 `Workflow started`，且 invoke event
绑定同一个 Task 和选定 pattern。

## 9. 启动 Runtime 与观测（需要真实 Worker 时）

创建 Project 或启动 Dashboard 不会自动拉起 worker。需要真实执行时，从 Project
根目录运行：

```bash
cd /path/to/project
uv run --project "$ZAOFU_ROOT" zf validate --cold-start
uv run --project "$ZAOFU_ROOT" zf start
```

另一个终端观测：

```bash
uv run --project "$ZAOFU_ROOT" zf status --workers
uv run --project "$ZAOFU_ROOT" zf kanban --board
uv run --project "$ZAOFU_ROOT" zf events --last 30
```

`zf start` 只启动 worker、sidecar 和 watcher。没有已批准的
`workflow.invoke.requested` 时，worker idle 是正确状态。

停止：

```bash
uv run --project "$ZAOFU_ROOT" zf stop
```

只有优雅停止失败时才使用 `zf stop --force`。共享主机不要执行
`tmux kill-server`。

## CLI 创建 Project

Web greenfield、`zf project init` 和 `tools/init-project.sh` 共用
`init_flow_project` 的 Project 初始化语义。CLI 示例：

```bash
uv run --project "$ZAOFU_ROOT" zf project init \
  --name account-service \
  --description "账号与认证服务；目标是逐步统一登录和安全策略。" \
  --root /path/to/account-service \
  --create \
  --git-init \
  --backend codex \
  --verify-backend claude-code \
  --stack python \
  --workspace-register
```

这条命令只创建 Project，不创建 Task，不点火 Workflow。

## 下一步

- [Project 创建、Bootstrap 与 Workflow 点火](20-project-bootstrap-workflow-ignition.md)
- [`zf.yaml` 控制面与运行态](02-zf-yaml-control-plane.md)
- [Channel 协作](15-channel-collaboration.md)
- [Web、观测与 E2E](06-web-observability-e2e.md)
- [故障排查](07-troubleshooting.md)
