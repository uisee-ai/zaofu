# ZaoFu CLI 使用手册

> 定位: ZaoFu CLI 命令**全量参考**(reference)。命令面以
> `uv run zf --help` / `src/zf/cli/main.py` 为准;本文按主题归类常用命令,
> 新增命令可能先出现在 `--help` 再回填本文。

只想快速启动先读 [01-quickstart.md](01-quickstart.md);日常操作流程见
[03-cli-operations.md](03-cli-operations.md);整体架构见 [架构总览](architecture.md)。

## 1. 基本约定

推荐在仓库根目录执行:

```bash
uv run zf --help
uv run zf <command> --help
```

如果已经安装 editable package,也可以直接用 `zf`。本文统一写
`uv run zf`。

ZaoFu 的 CLI 有三条边界:

- `zf.yaml` 是唯一控制面配置。
- 运行态目录来自 `project.state_dir`,默认是 `.zf/`;不要在代码里硬编码。
- `events.jsonl`、`kanban.json`、`session.yaml`、`feature_list.json`、
  `role_sessions.yaml` 是 kernel 管理的 canonical state,日常变更优先走
  `zf` 命令,不要手工编辑。

常见参数:

| 参数 | 说明 |
|---|---|
| `--state-dir PATH` | 指向运行态目录,用于临时模拟或多项目排障 |
| `--json` / `--format json` | 输出机器可读结果 |
| `--dry-run` | 只检查/预览,不执行真实动作 |
| `--help` | 查看当前命令参数 |
| `--version` | 查看 CLI 版本 |

## 2. 最短可用流程

新项目:

```bash
uv run zf presets
uv run zf init --preset safe-team
uv run zf validate --cold-start
uv run zf start --dry-run --no-watch
uv run zf start
```

给 harness 一个任务:

```bash
uv run zf chat "实现一个小功能,包含测试、review 和最终 gate"
uv run zf kanban --board
uv run zf events --last 20
```

人工创建并分配 task:

```bash
TASK_ID="$(uv run zf kanban add "修复登录态过期问题" --id-only)"
uv run zf kanban assign "$TASK_ID" dev
uv run zf task trace "$TASK_ID"
```

停止:

```bash
uv run zf stop
```

## 3. 配置与启动前检查

| 命令 | 用途 |
|---|---|
| `uv run zf presets` | 列出内置 preset |
| `uv run zf presets show <name>` | 输出某个 preset 的 YAML |
| `uv run zf init [PATH] [--create] [--preset NAME] [--state-dir PATH]` | 初始化项目运行态,并补齐 `AGENTS.md` / `CLAUDE.md` |
| `uv run zf init --skip-instruction-docs` | 只初始化运行态,不创建/刷新项目指令文档 |
| `uv run zf init --workspace-register` | 初始化后注册到 workspace |
| `uv run zf init --env-check` | 初始化时执行环境探测 |
| `uv run zf profile detect` | 探测项目技术栈 |
| `uv run zf profile recommend` | 推荐 profile/preset |
| `uv run zf profile bootstrap` | 生成 bootstrap 建议 |
| `uv run zf validate --path zf.yaml` | 校验配置文件 |
| `uv run zf validate --cold-start` | 冷启动 readiness 检查 |
| `uv run zf validate --strict-skills` | skill 问题按失败处理 |
| `uv run zf validate --strict-contracts` | 检查 active task contract |
| `uv run zf validate --architecture` | 运行架构规则检查 |
| `uv run zf validate --instructions` | lint 指令文件 |
| `uv run zf doctor workdirs` | 检查 workdir 健康 |
| `uv run zf doctor panes` | 检查 tmux pane 绑定 |
| `uv run zf agents` | 探测可用 agent CLI |

建议启动前至少跑:

```bash
uv run zf validate --cold-start
uv run zf skills doctor
uv run zf gate list
```

## 4. Runtime 生命周期

| 命令 | 用途 |
|---|---|
| `uv run zf start --dry-run` | 检查并记录启动动作,不启动真实 worker |
| `uv run zf start` | 启动 harness loop 并前台运行 watcher |
| `uv run zf start --no-watch` | spawn workers 后退出,不长期运行 watcher |
| `uv run zf status` | 当前 session/task 概览 |
| `uv run zf status --workers` | worker / role session 概览 |
| `uv run zf attach [role]` | attach 到 tmux session/pane |
| `uv run zf logs [role] --tail 100` | 查看 harness 或 role 日志 |
| `uv run zf restart [role]` | 重启整个 harness 或单个 role |
| `uv run zf stop` | 优雅停止 |
| `uv run zf stop --force` | 强制停止并清理 lock |

`--foreground` 仍兼容,但当前只是 deprecated no-op alias。stream-json / headless backend 不一定有可 attach 的 tmux pane。此时优先用
`zf watch`、`zf events`、`zf trace` 和 Web dashboard 看运行态。

## 5. Feature 与 Task

Feature 是高层用户目标;Task 是可执行单元。不要引入第二套 task schema。

### 5.1 通过提示词提交任务

最常用入口是 `zf chat`。它把自然语言 prompt 写成 `user.message` 事件,
再由当前 workflow/orchestrator 决定是创建 task、补充 contract、请求澄清,
还是进入 arch/critic/dev 等后续路径。

一句话提交:

```bash
uv run zf chat "新增一个稳定的 Web action token 配置,要求更新文档并补测试"
```

长 prompt 提交:

```bash
cat >/tmp/zf-task-prompt.md <<'EOF'
目标:
  改进 Channel Add Agent,支持 arch channel role。

验收:
  1. channel_roles/arch.md 存在,包含 Forbidden / Stop Rule。
  2. Web Add Agent 可以选择 Arch。
  3. backend contract 接受 channel_role=arch。
  4. 补充测试并通过。

约束:
  不新增 zf.yaml workflow role;arch 只是 channel role。
EOF

uv run zf chat "$(cat /tmp/zf-task-prompt.md)"
uv run zf events --last 20
uv run zf kanban --board
```

确认结果时看三处:

- `zf events --last 20`: 是否有 `user.message`、`task.created` 或澄清事件。
- `zf kanban --board`: 是否出现新 task 或状态变化。
- `zf task trace <task_id>`: task 出现后查看从 prompt 到执行的因果链。

如果你要的是**确定性地创建一个 task**,不要等 orchestrator 从自然语言推断,
直接用 `zf kanban add`:

```bash
TASK_ID="$(uv run zf kanban add "支持 arch channel role" --id-only)"
uv run zf kanban assign "$TASK_ID" arch
```

如果你要把一份较长的设计/任务说明变成结构化 ZaoFu tasks,用 `zf spec`:

```bash
uv run zf spec prompt docs/specs/arch-channel-role.md >/tmp/zf-spec-prompt.txt
# 把 /tmp/zf-spec-prompt.txt 交给任意 LLM,只保存它返回的 JSON frontmatter:
# /tmp/arch-channel-role.frontmatter.json

uv run zf spec merge \
  docs/specs/arch-channel-role.md \
  --frontmatter /tmp/arch-channel-role.frontmatter.json \
  --output /tmp/arch-channel-role.zf-spec.md

uv run zf spec validate /tmp/arch-channel-role.zf-spec.md --strict
uv run zf spec ingest /tmp/arch-channel-role.zf-spec.md
uv run zf kanban --board
```

选择规则:

- `zf chat`: 适合 operator 通过 prompt 给 harness 下达目标,保留 agent 判断空间。
- `zf kanban add`: 适合已经明确的单个 task,确定性写入 TaskStore。
- `zf spec ingest`: 适合从长设计/PRD/任务提示词批量生成规范 task。

| 命令 | 用途 |
|---|---|
| `uv run zf feature add <title>` | 创建 feature |
| `uv run zf feature list [--status STATUS]` | 列出 feature |
| `uv run zf feature show <feature_id>` | 查看 feature |
| `uv run zf feature update <feature_id> ...` | 更新 feature |
| `uv run zf kanban` | 列出 active tasks |
| `uv run zf kanban --board` | 看板视图 |
| `uv run zf kanban --watch --board` | watch 看板 |
| `uv run zf kanban add <title>` | 新建 task |
| `uv run zf kanban add <title> --feature F-xxx` | 新建并关联 feature |
| `uv run zf kanban add <title> --blocked-by TASK-1 TASK-2` | 新建带阻塞关系的 task |
| `uv run zf kanban assign <task_id> <role>` | 分配 task |
| `uv run zf kanban move <task_id> <status>` | 移动 task 状态 |
| `uv run zf kanban show <task_id>` | 查看 task 详情 |
| `uv run zf kanban ready` | 列出 ready tasks |
| `uv run zf kanban open` | 列出非终态 tasks |
| `uv run zf kanban pending` | 列出 backlog tasks |
| `uv run zf kanban export --format md` | 导出 kanban 报告 |
| `uv run zf kanban health --format md` | 输出 kanban health |
| `uv run zf task trace <task_id>` | 查看 task 因果链 |

`kanban move done` 不是自由终态写入。若当前 workflow 要求
`review.approved`、`test.passed`、`judge.passed` 或 discriminator 证据,
CLI/Kernel 会拒绝缺证据的终态推进。

## 6. 事件、Watch 与 Trace

`zf emit` 是 worker / operator 向 append-only event log 上报事实的入口。
优先发事件,让 Layer 1 projection 更新状态。

| 命令 | 用途 |
|---|---|
| `uv run zf events --last N` | 查看最近 N 条事件 |
| `uv run zf events --type TYPE` | 按事件类型过滤 |
| `uv run zf events trace <event_id>` | 查看事件 causation chain |
| `uv run zf emit <type> --task <id> --actor <role>` | 追加事件 |
| `uv run zf emit <type> --payload '{"k":"v"}'` | 用 JSON payload 追加事件 |
| `uv run zf emit <type> --payload-file payload.json` | 从文件读取 payload |
| `uv run zf watch --last N --follow` | tail event log |
| `uv run zf watch --role ROLE` | 按 actor 过滤 |
| `uv run zf watch --task TASK_ID` | 按 task 过滤 |
| `uv run zf watch --type TYPE` | 按事件类型过滤 |
| `uv run zf trace show <trace_id>` | 查看 trace |
| `uv run zf trace operation <dispatch_id>` | 查看某次 dispatch |
| `uv run zf trace spans` | 查看 span 投影 |
| `uv run zf trace gantt --format mermaid` | 输出 gantt / dag |

示例:

```bash
uv run zf emit dev.blocked \
  --task TASK-ABCDEF \
  --actor dev-1 \
  --payload '{"reason":"missing dependency"}'
```

## 7. Handoff、Memory 与 Skills

| 命令 | 用途 |
|---|---|
| `uv run zf handoff --format md` | 生成当前交接摘要 |
| `uv run zf handoff --format state-packet --task TASK_ID` | 生成 task state packet |
| `uv run zf handoff --score` | 带恢复充分性评分 |
| `uv run zf memory show [role]` | 查看 memory,默认 shared |
| `uv run zf memory add <role|shared> <text>` | 增加 memory |
| `uv run zf memory check` | 检查 memory staleness |
| `uv run zf skills list` | 查看 role skill 解析结果 |
| `uv run zf skills list --json` | JSON 输出 |
| `uv run zf skills doctor` | 检查缺失/冲突 skill |
| `uv run zf update agents-md --check` | 检查 AGENTS.md managed block |
| `uv run zf update agents-md --write` | 重写 AGENTS.md managed block |

Memory 和 skills 是 worker 上下文材料,不是第二控制面。修改 workflow topology
仍应回到 `zf.yaml`。

## 8. Gate、Metrics 与 Cost

| 命令 | 用途 |
|---|---|
| `uv run zf gate list` | 列出 quality gates |
| `uv run zf gate run <name>` | 运行单个 gate |
| `uv run zf gate run all` | 运行所有 gate |
| `uv run zf cost` | 成本汇总 |
| `uv run zf cost --days 7` | 近 7 天成本 |
| `uv run zf cost --by-instance` | 按 role instance 拆分 |
| `uv run zf cost --by-backend` | 按 backend 拆分 |
| `uv run zf metrics snapshot` | long-horizon 指标快照 |
| `uv run zf metrics snapshot --format json` | JSON 指标 |
| `uv run zf metrics snapshot --diff baseline.json` | 与 baseline 对比 |
| `uv run zf metrics diagnose` | 输出指标诊断 |
| `uv run zf metrics decision-ratio --by-reason` | 决策比例分析 |

`zf cost` 和 `zf metrics` 是观测入口。预算 hard block 由 runtime dispatch 前检查,
不是靠人工运行查询命令实现。

## 9. Workdir、Pane、Run Archive 与 State

| 命令 | 用途 |
|---|---|
| `uv run zf doctor workdirs` | workdir 健康检查 |
| `uv run zf doctor panes` | pane-grid 检查 |
| `uv run zf panes doctor` | pane binding 专用检查 |
| `uv run zf panes repair` | 从 live tmux pane 修复 pane bindings |
| `uv run zf workdir repair <instance>` | 修复指定 instance workdir |
| `uv run zf refs verify` | 验证 task/candidate git refs |
| `uv run zf runs list` | 查看 run archive 投影 |
| `uv run zf runs rebuild` | 重建 run projection |
| `uv run zf runs reconcile` | 标记 stale active runs |
| `uv run zf runs for-task <task_id>` | 查看某 task 的 runs |
| `uv run zf archive-run --run-id RUN --live-state-dir PATH` | 归档 live run state |
| `uv run zf state clean --dry-run` | 预览可清理 projection |
| `uv run zf state clean --confirm --archive` | 清理可重建 projection 并归档 |
| `uv run zf state reconcile --dry-run` | 检查 state projection 一致性 |

清理前确认只处理 rebuildable projection,不要删除 truth files。

## 10. Web、Workspace 与外部集成

| 命令 | 用途 |
|---|---|
| `uv run zf web --host 127.0.0.1 --port 8001` | 启动本地 Web dashboard |
| `uv run zf web --host 0.0.0.0 --port 8001` | 暴露给容器/局域网 |
| `uv run zf web --workspace-only` | 只启动 workspace 视图 |
| `uv run zf workspace providers openclaw list` | 查看 workspace OpenClaw binding |
| `uv run zf workspace providers openclaw set remote --base-url URL --timeout-seconds 120` | 写入 OpenClaw binding，默认等待 120 秒 |
| `uv run zf feishu bridge --watch` | **直连飞书常驻 bridge**(长连接,群/单聊流式问答 + 点按钮审批,无需 OpenClaw / webhook;见手册 19) |
| `uv run zf feishu handle` | 处理飞书事件 payload |
| `uv run zf feishu push --watch` | 从 events 直连推送飞书通知 / 卡片(FeishuHttpTransport) |
| `uv run zf feishu serve --host 0.0.0.0 --port 8000` | 启动飞书 webhook server(公网 webhook 模式,长连接见 `bridge --watch`) |
| `uv run zf feishu send-test --message "hello"` | 发送测试消息 |
| `uv run zf feishu init-targets --transport real --write-env` | 创建 Automation 文档、Kanban 多维表格和字段,并写入 `.env` |
| `uv run zf feishu sync-automations --dry-run` | 预览 daily/weekly/project Automation 飞书文档输出 |
| `uv run zf feishu sync-automations --transport real --document-id "$FEISHU_AUTOMATION_DOCUMENT_ID"` | 同步 Automation 报告到飞书文档 |
| `uv run zf feishu sync-automations --transport real --document-url "$FEISHU_AUTOMATION_DOCUMENT_URL"` | 通过飞书文档 URL 同步 Automation |
| `uv run zf feishu sync-automation-insights-table --dry-run` | 预览 Automation insight 多维表格输出 |
| `uv run zf feishu sync-kanban-table --dry-run` | 预览 Kanbanboard 飞书表格输出 |
| `uv run zf feishu sync-kanban-table --transport real --app-token "$FEISHU_BITABLE_APP_TOKEN" --table-id "$FEISHU_BITABLE_TABLE_ID"` | 同步 Kanbanboard 到飞书多维表格 |
| `uv run zf feishu sync-kanban-table --transport real --bitable-url "$FEISHU_BITABLE_URL"` | 通过飞书多维表格 URL 同步 Kanbanboard |
| `uv run zf feishu cron-template` | 生成每日 Automation + 每小时 Kanbanboard cron 示例 |
| `uv run zf hook-recv --event EVENT` | 从 stdin 接 Claude Code hook JSON |

Web 里的写动作应走 token-gated action path。常用本地启动脚本:

```bash
tools/start-webkanban.sh --no-build
```

如需固定本地 Web/Kanban Agent action token,在仓库 `.env` 写入
`ZF_WEB_ACTION_TOKEN=...`。不要提交 `.env`。

该 gate 不只覆盖任务 action。以下会探测主机路径或修改 host-global
Workspace 状态的入口同样需要 token/passcode/trusted session:

- Bootstrap Inspect、Project Validate Path。
- Onboarding 的 step/complete/skip/reset。
- Project Touch（最近访问项目）。
- Project register/init/remove。

首次打开 Welcome Wizard 时可在向导顶部保存 action token；无效 token 会
显示 403 原因，不会静默跳过向导。

## 11. Spec、Backlog 与 Operator 辅助

| 命令 | 用途 |
|---|---|
| `uv run zf spec validate <path>` | 校验 structured spec markdown |
| `uv run zf spec ingest <path>` | 从 spec 生成 ZaoFu tasks |
| `uv run zf spec prompt <path>` | 生成 worker prompt |
| `uv run zf spec merge <path> --frontmatter fm.yaml` | 合并 frontmatter/spec |
| `uv run zf backlog audit` | 审计 backlog/task 文档 |
| `uv run zf backlog why-not-done <task_id>` | 解释 task 未完成原因 |
| `uv run zf backlog resume-packet <task_id>` | 生成恢复包 |
| `uv run zf backlog integration <feature_id>` | feature 集成视图 |
| `uv run zf backlog workpad <task_id>` | task workpad |
| `uv run zf backlog retry-metadata <task_id>` | retry metadata |
| `uv run zf backlog goal <feature_id>` | feature goal 视图 |
| `uv run zf guard ownership --task <task_id> --role <role>` | 校验当前 worker 是否拥有 task/role/workdir 写入权 |
| `uv run zf artifact manifest create --task <task_id> --role <role> --kind kind=path` | 为 plan/spec/backlog 等 artifact 生成 manifest |
| `uv run zf project review-spine --dry-run` | 生成 project spine review 预览 |
| `uv run zf bug-fix-cycle --signature SIG` | zaofu bug fix cycle 辅助 |
| `uv run zf autopilot tick --dry-run` | deterministic proposal-only 自检 |

`backlogs/` 是本地 candidate,`tasks/` 是 active/done sprint 文档。批准后用
`git mv backlogs/<file>.md tasks/`。

## 12. Self-Eval 与 Autoresearch

| 命令 | 用途 |
|---|---|
| `uv run zf self-eval validate --contract file.yaml` | 校验 self-eval contract |
| `uv run zf self-eval run --contract file.yaml` | 执行 self-eval |
| `uv run zf autoresearch run --worktree PATH --scenario S` | 运行外层 autoresearch |
| `uv run zf autoresearch discover-bugs` | 从运行结果发现 bug candidates |
| `uv run zf autoresearch triggers scan` | 只读扫描触发器 |
| `uv run zf autoresearch self-repair prepare --trigger T` | 进入 self-repair 准备 |
| `uv run zf autoresearch self-repair checkpoint --task TASK --role ROLE` | 写入修复 checkpoint |
| `uv run zf autoresearch self-repair validate --repair-run RUN --passed` | 标记修复验证 |
| `uv run zf autoresearch loop --scenarios S1.yaml --worktree PATH` | 多轮 scenario loop |
| `uv run zf autoresearch campaign plan --output-dir DIR` | 生成 campaign plan |

这类命令通常会占用临时 worktree、tmux session 和预算。临时模拟请使用
`/tmp/zf-<purpose>-<utc-timestamp>/`,并清理临时 session。

## 13. 顶层命令目录

当前 `zf --help` 注册的顶层命令:

```text
init, validate, status, emit, events, start, stop, restart, kanban, gate,
cost, memory, handoff, presets, attach, logs, rules, check, cleanup, agents,
watch, feature, chat, hook-recv, trace, doctor, workdir, refs, workflow,
archive-run, runs, feishu, autopilot, skills, state, self-eval, panes,
autoresearch, update, guard, artifact, metrics, task, web, spec,
bug-fix-cycle, backlog, workspace, project
```

常用子命令速查:

| 命名空间 | 子命令 |
|---|---|
| `events` | `trace` |
| `kanban` | `add`, `move`, `assign`, `show`, `ready`, `open`, `pending`, `export`, `health` |
| `feature` | `add`, `list`, `show`, `update` |
| `trace` | `show`, `record-fixture`, `replay-fixture`, `spans`, `operation`, `gantt` |
| `workflow` | `render`, `audit` |
| `runs` | `list`, `rebuild`, `reconcile`, `for-task` |
| `feishu` | `handle`, `push`, `serve`, `send-test`, `init-targets`, `sync-automations`, `sync-automation-insights-table`, `sync-kanban-table`, `cron-template` |
| `skills` | `list`, `doctor` |
| `state` | `clean`, `reconcile` |
| `autoresearch` | `run`, `discover-bugs`, `triggers`, `self-repair`, `loop`, `campaign` |
| `guard` | `ownership` |
| `artifact` | `manifest create` |
| `metrics` | `snapshot`, `diagnose`, `decision-ratio` |
| `spec` | `ingest`, `validate`, `prompt`, `merge` |
| `backlog` | `audit`, `why-not-done`, `resume-packet`, `integration`, `workpad`, `retry-metadata`, `goal` |
| `workspace providers openclaw` | `list`, `set` |
| `project` | `review-spine` |

## 14. 排障顺序

1. CLI 参数不确定: `uv run zf <command> --help`
2. 配置问题: `uv run zf validate --cold-start`
3. Worker 没响应: `uv run zf status --workers` + `uv run zf watch --follow`
4. Task 卡住: `uv run zf task trace <task_id>` + `uv run zf backlog why-not-done <task_id>`
5. Workdir/pane 异常: `uv run zf doctor workdirs` + `uv run zf doctor panes`
6. Projection 异常: `uv run zf state reconcile --dry-run`
7. Web 看不到项目: 确认在项目根目录启动,或已 `zf init --workspace-register`
