# Feishu Automation / Kanban Sync 使用手册

> 状态: active
> 适用范围: 把 ZaoFu 的只读投影同步到飞书文档和飞书多维表格。
> 主入口: 飞书直连链路见 [19-feishu-ai-native-direct-bridge.md](19-feishu-ai-native-direct-bridge.md)。本文只保留 Automation / Kanban 同步专题细节。

## 边界

本功能只做单向同步:

- Automations 的 `daily-brief`、`weekly-review`、`project-monitor` 汇总为轻量 Overview,追加到飞书文档。
- Automation 的 Project Status / Action Required / Delivery Health / Runtime Health 结构化同步到 Automation Insights 多维表格,这是飞书侧的主入口。
- 当前 Kanban projection 渲染为多维表格记录,以 `Task ID` 为稳定键创建或更新行。
- 飞书文档 / 表格不是 ZaoFu 的控制面,不会反向修改 `events.jsonl`、`kanban.json` 或任务状态。

Automation 文档定位是项目 Overview 封面,不是原始运行日志或诊断台。飞书
多维表格才是日常查看入口,默认使用 `ZaoFu Overview`、`ZaoFu Action Required`、
`ZaoFu Highlights`、`ZaoFu Delivery Health`、`ZaoFu Runtime Health`、
`ZaoFu History` 六个视图组织总览、高亮事项、人工介入、交付健康、运行健康
和历史查询。
详细 trace、events、agent/session、task drilldown 留在 ZaoFu Web / CLI;
详细任务列表仍以 Kanban 多维表格为准。

同步前先在 Web `Automations` 核对 Daily Brief、Weekly Review 和 Project Monitor
的 schedule、execution health、insights、outputs 与 recent runs：

![Daily、Weekly 与 Project Monitor 自动化报告动态演示](assets/automations-reports.webp)

## 环境变量

真实同步需要飞书应用凭据。可以直接 `export`,也可以写到项目根目录 `.env`;
`zf feishu` 会在命令启动时读取 `.env`,但不会覆盖 shell 已有环境变量。

```bash
export FEISHU_APP_ID="cli_xxx"
export FEISHU_APP_SECRET="xxx"
export FEISHU_AUTOMATION_DOCUMENT_ID="docx_xxx"
export FEISHU_AUTOMATION_BITABLE_APP_TOKEN="bascn_xxx"
export FEISHU_AUTOMATION_BITABLE_TABLE_ID="tbl_xxx"
export FEISHU_BITABLE_APP_TOKEN="bascn_xxx"
export FEISHU_BITABLE_TABLE_ID="tbl_xxx"
export FEISHU_FOLDER_TOKEN="fld_xxx"
```

也可以使用 URL,CLI 会自动解析文档 token、Base token 和 `table=` 查询参数:

```bash
export FEISHU_AUTOMATION_DOCUMENT_URL="https://xxx.feishu.cn/docx/docx_xxx"
export FEISHU_AUTOMATION_BITABLE_URL="https://xxx.feishu.cn/base/bascn_xxx?table=tbl_xxx"
export FEISHU_BITABLE_URL="https://xxx.feishu.cn/base/bascn_xxx?table=tbl_xxx"
```

也可以直接设置 `FEISHU_TENANT_ACCESS_TOKEN`,跳过 app_id/app_secret 换 token。

Docx/Base 真实同步推荐使用 `lark-cli` backend。需要确保 `lark-cli >= 1.0.47`
可从 `PATH` 找到。ZaoFu 会把上述凭据映射给子进程,固定使用 Feishu 品牌、
bot 身份和 JSON 输出,不会把 secret 放进命令参数。只有 app ID/secret 时,
ZaoFu 会 mint 并短时复用 tenant token;显式
`FEISHU_TENANT_ACCESS_TOKEN` 优先。

```bash
lark-cli --version
```

Backend 含义:

- `--backend lark-cli`: 推荐的 Docx/Base 外部执行端口。
- `--backend mock`: 只用于本地 deterministic 测试。
- 旧 `--transport real` 仍兼容,等价于 `--backend lark-cli`;不要同时传
  `--backend` 和 `--transport`。

飞书群聊、流式卡片、WebSocket、callback 和 approval 仍使用 ZaoFu 原生
`FeishuHttpTransport`,不会经过 `lark-cli`。已经移除的只是 Docx/Base 原生
投影 client；实时消息 transport 仍是必要组件。

## Project 协作群与 Workspace Bridge

Automation / Kanban 投影之外，ZaoFu 可以为一个 Project 显式管理一个 Feishu
协作群。当前 `bot_purposes` 仅支持 `kanban_agent`（Kanban Agent App bot）和
`run_manager`（Run Manager App bot），真实群因此只包含 Owner 与这两个 bot；
Codex、Claude Code、OpenClaw 等内部 Channel member 由这些 bot 代理，不会伪装成
Feishu 群成员。

项目 `zf.yaml`（或被 loader 合并的 `feishu.yaml`）配置如下。默认
`auto_provision: false`，`zf init` 只注册项目并创建 pending binding，不会调用
飞书创建群：

```yaml
runtime:
  feishu_inbound:
    enabled: true

integrations:
  feishu_project_group:
    enabled: true
    auto_provision: false
    name_template: "ZaoFu - {project_name}"
    owner_open_id_env: ZF_FEISHU_PROVISIONER_OWNER_OPEN_ID
    provisioner_purpose: run_manager
    bot_purposes: [kanban_agent, run_manager]
    primary_responder: kanban_agent
```

配置所需环境变量：

```bash
export ZF_FEISHU_PROVISIONER_OWNER_OPEN_ID="ou_xxx"
export FEISHU_RUNM="cli_run_manager_app"
export FEISHU_RUNM_SECRET="..."
export FEISHU_KANBAN="cli_kanban_app"
export FEISHU_KANBAN_SECRET="..."
```

先初始化并检查本地 binding：

```bash
uv run zf init --workspace default
uv run zf feishu group status
```

只有下面两种路径会写入真实飞书：配置明确设置 `auto_provision: true`，或者
操作员执行带 `--confirm` 的命令。两条路径都会在 create / attach 后回读成员；
Owner 和全部 bot 没有同时验证成功时状态保持 `repair_required`：

```bash
# 创建新的协作群并验证成员
uv run zf feishu group provision --workspace default --confirm

# 使用一个已有群，补齐并验证成员
uv run zf feishu group attach --chat-id oc_xxx --confirm
```

已存在 binding 时，省略 `--workspace` 会沿用 binding 记录的 Workspace，避免
把多项目路由错误迁回 `default`。首次为非默认 Workspace 显式 provision 时仍应传入
`--workspace <workspace-id>`。

### 自动建群的状态与回读

`auto_provision: true` 会让 `zf init` 变成一次**明确配置过的外部写操作**，而不是普通的
本地初始化。它按如下状态收敛；可以随时用 `zf feishu group status` 回读：

| binding 状态 | 含义 | 操作员动作 |
|---|---|---|
| `pending` | 已保存 desired topology，尚未调用飞书 | 设置 `auto_provision: true` 后重新 init，或执行 `group provision --confirm` |
| `provisioning` | 正在创建/附着群并验证成员 | 等待本次命令结束；不要并发再起一个 provision |
| `active` | Owner、全部配置 bot 已回读验证，且 Workspace route index 已成功重建 | 用 `zf start` 启动/维持入站 sidecar |
| `repair_required` | 凭据、权限、跨 App open_id、成员或路由冲突未通过验证 | 查看 `status` 中的 `error`，修正后重新执行 `group provision --confirm` |

禁用配置不会删除已有飞书群，也不会删除 canonical event 或项目 binding；群的创建、附着和
成员补齐始终是受控外部副作用。需要把已有群交给 ZaoFu 时，用 `group attach --chat-id ... --confirm`
回读验证，而不是手工填写运行时 `chat_id`。

项目群需要 `im:chat:create`、`im:chat.members:read`、
`im:chat.members:write_only` 权限，并要求 `lark-cli >= 1.0.64`（这是
`im +chat-members-list` 成员回读命令的首个版本）。`lark-cli` 仅被允许执行群
创建、成员列表和成员添加的固定 argv；secret 不会进入状态文件或命令参数。
这些群管理权限必须由 `provisioner_purpose` 对应的 Bot 获批。`owner_open_id_env`
中的值也必须是该 Bot 可见的 app-scoped open_id，不能复用另一个飞书 App 看到的
ID；否则飞书会返回 `open_id cross app`，ZaoFu 会保持 `repair_required`。

binding 激活后执行 `zf start`。同一主机上，任意 Workspace 的多个 Project 若复用
同一个 Feishu App，只会保留一条 `(host, app_id)` WebSocket；入站消息由精确
`(app_id, chat_id)` binding index 聚合后选择正确 Project。历史项目未启用这个配置
时，继续使用既有 `integrations.feishu_routing` 路径，但也不能与已有 App bridge
并发连接。协作群中未 @ bot 的普通消息由 `primary_responder` 处理；显式 @ Kanban
Agent 或 Run Manager 时由对应 bot 处理。

后启动的 Project 只加入 provider bridge 租约，先启动的 Project 停止时若还有其他
Workspace/Project 租约，连接会继续服务；只有最后一个租约释放时才停止共享
WebSocket。不要另行手工启动同 App 的 bridge；若需要诊断，先停止 managed
sidecar，然后使用以下唯一入口：

```bash
uv run zf feishu bridge --watch --all-workspaces --app-id "$FEISHU_APP_ID"
```

每个项目的 restart catch-up cursor 按 `(app_id, chat_id)` 保存，因此同一协作群内
的不同 bot 不会互相跳过未处理消息。

群内回复是面向人的投影：未 @ bot 的普通消息由 `primary_responder` 处理，显式 @ ZF 产品经理或
ZF 架构师才进入对应 target。长 PRD、评审和扫描结果不应依赖一张聊天卡完整承载；详情与长文输出
边界见 [19-feishu-ai-native-direct-bridge.md](19-feishu-ai-native-direct-bridge.md)。

## 初始化飞书目标

如果还没有文档或多维表格,先用显式初始化命令创建目标,不要让 daily/hourly
同步命令隐式创建外部资源:

```bash
uv run zf feishu init-targets \
  --backend lark-cli \
  --write-env
```

常用参数:

- `--folder-token <token>`: 指定飞书云盘目录;不指定则由飞书应用默认位置决定。
- `--document-title <title>`: Automation 文档标题。
- `--base-name <name>`: Kanban 多维表格名称。
- `--table-name <name>`: Kanban 表名称,默认 `Kanban`。
- `--automation-table-name <name>`: Automation Insights 表名称,默认 `Automation Insights`。
- `--field key=字段名`: 创建字段前覆盖默认字段名。
- `--overwrite-env`: `.env` 已有 target key 时允许覆盖。

初始化会创建:

- Automation 文档,用于追加 daily / weekly / project 报告。
- Automation Insights Table,并补齐 summary / insight / highlight 字段和六个推荐视图。
- Kanban Base + Table,并补齐 `Task ID`、`Title`、`Status`、`Assigned To`
  等同步字段,以及用于飞书 Kanban 分组的 `Board Column` 单选字段。
- Kanban Table 默认创建 `ZaoFu Grid` 和 `ZaoFu Kanban` 两个视图。所有视图
  共享同一批记录,但飞书内的筛选、分组、排序可以按视图独立配置。
  初始化会同时设置推荐的可见字段、排序和 Kanban 分组。
- 使用 `--write-env` 时,把 `FEISHU_AUTOMATION_DOCUMENT_ID`、
  `FEISHU_AUTOMATION_BITABLE_APP_TOKEN`、`FEISHU_AUTOMATION_BITABLE_TABLE_ID`、
  `FEISHU_BITABLE_APP_TOKEN`、`FEISHU_BITABLE_TABLE_ID` 写入项目 `.env`。

先看计划但不调用飞书:

```bash
uv run zf feishu init-targets --dry-run
```

本地测试 CLI 写 `.env` 行为可用 mock:

```bash
uv run zf feishu init-targets --backend mock --write-env
```

真实创建需要飞书应用具备创建文档、多维表格、字段的 OpenAPI 权限。即使
`.env` 里有个人 app secret,OpenAPI 调用仍以该应用的 tenant token 身份执行;
若指定 `--folder-token`,还要确保应用对该云盘目录有写权限。

完整初始化至少涉及 `base:app:create`、`base:table:read/create/update/delete`。
对已有表执行投影至少涉及 `base:field:read/create`、`base:view:read`、
`base:view:write_only`、`base:record:read/create/update`;真实删除恢复测试另需
`base:record:delete`。scope 已开通仍不等于资源已授权,目标 Base/Table 还必须对
该应用可见。

只缺少 `base:view:write_only` 时,全量同步命令可通过
`--no-ensure-layouts` 同步记录、字段和视图,但不会自动设置筛选、可见字段、
排序和 Kanban 分组。常驻 projector 当前要求其配置的完整结构权限,不会静默
降级或切换应用。

## Dry Run

先在本地确认输出内容:

```bash
uv run zf feishu sync-automations --dry-run
uv run zf feishu sync-automation-insights-table --dry-run
uv run zf feishu sync-kanban-table --dry-run
```

只同步某一类 automation:

```bash
uv run zf feishu sync-automations \
  --dry-run \
  --automation daily-brief
```

## 真实同步

Automation 报告同步到飞书文档:

```bash
uv run zf feishu sync-automations \
  --backend lark-cli \
  --document-id "$FEISHU_AUTOMATION_DOCUMENT_ID"
```

也可以直接传文档 URL:

```bash
uv run zf feishu sync-automations \
  --backend lark-cli \
  --document-url "$FEISHU_AUTOMATION_DOCUMENT_URL"
```

Automation Insights 同步到飞书多维表格:

```bash
uv run zf feishu sync-automation-insights-table --backend lark-cli
```

如果 `.env` 已有 `FEISHU_BITABLE_APP_TOKEN` 但还没有
`FEISHU_AUTOMATION_BITABLE_TABLE_ID`,命令会在同一个 Base 下创建
`Automation Insights` 表、补齐字段,并写回
`FEISHU_AUTOMATION_BITABLE_APP_TOKEN` 和 `FEISHU_AUTOMATION_BITABLE_TABLE_ID`。
后续同步按 `Row Key` 更新当天的 summary / insight 行。
默认会确保六个分析视图存在:

- `ZaoFu Overview`: 只看 summary 行。
- `ZaoFu Highlights`: 通过 `Highlight` 彩色单选字段聚合 P0、决策、阻塞、
  运行告警、频道关注和交付风险。
- `ZaoFu Action Required`: 只看 critical/error/warn 事项。
- `ZaoFu Delivery Health`: 聚焦 weekly-review。
- `ZaoFu Runtime Health`: 聚焦 project-monitor。
- `ZaoFu History`: 按日期查看历史 summary / insight。

只同步某一类 automation:

```bash
uv run zf feishu sync-automation-insights-table \
  --backend lark-cli \
  --automation daily-brief
```

Kanban 同步到飞书多维表格:

```bash
uv run zf feishu sync-kanban-table \
  --backend lark-cli \
  --app-token "$FEISHU_BITABLE_APP_TOKEN" \
  --table-id "$FEISHU_BITABLE_TABLE_ID"
```

或直接传多维表格 URL:

```bash
uv run zf feishu sync-kanban-table \
  --backend lark-cli \
  --bitable-url "$FEISHU_BITABLE_URL"
```

默认会同步 active board 加最近 30 天 terminal archive,避免刚完成的 task
因归档而留在表格旧状态。同步前还会确保:

- `Board Column` 单选字段存在,值来自 ZaoFu task status 映射后的看板列名。
- `ZaoFu Grid` 表格视图存在,适合批量筛选和编辑显示列。
- `ZaoFu Kanban` 看板视图存在,适合按 `Board Column` 分组查看任务流转。
- `ZaoFu Grid` / `ZaoFu Kanban` 的可见字段和排序按推荐布局配置。

Gantt 视图暂不默认创建。飞书 Gantt 需要明确的日期型 `start_time` /
`end_time` 字段;当前 Kanban 同步里的 `Started At` / `Completed At` 仍按
文本兼容旧表写入,不适合直接当作 Gantt 时间轴。只想镜像 active
`kanban.json` 时使用:

```bash
uv run zf feishu sync-kanban-table --backend lark-cli --active-only
```

### Issue / Triage / Merge 多维表格与 Gantt

当前 `/issue <描述>` Portable Session、外部 Issue Triage、候选 PR 和
`delivery.merged` 生命周期可以投影到独立的 `Issue` 表。该表不会写回
Issue Session、Issue Mirror 或事件状态，只读取 canonical Store 和
`events.jsonl`，再通过 `EventWriter` 记录同步事件。

先确认目标表（推荐使用独立表，不要复用 Kanban 表）:

```bash
export FEISHU_ISSUE_BITABLE_APP_TOKEN="$FEISHU_BITABLE_APP_TOKEN"
export FEISHU_ISSUE_BITABLE_TABLE_ID="tbl_issue_xxx"
uv run zf feishu sync-issue-table --backend lark-cli
```

需要持续刷新时单独运行 Issue projector；它按轮询间隔读取 canonical state，
不会阻塞 `feishu bridge --watch` 的群消息和卡片刷新：

```bash
uv run zf feishu sync-issue-table --backend lark-cli --watch \
  --poll-interval-seconds 5
```

`--watch` 需要保持该进程运行；bridge 本身不会隐式访问 Bitable。

也可以先本地查看即将写入的行:

```bash
uv run zf feishu sync-issue-table --dry-run
```

首次使用现有 Base 时可让命令创建 `Issue` 表并补齐字段和视图:

```bash
uv run zf feishu sync-issue-table \
  --backend lark-cli \
  --app-token "$FEISHU_ISSUE_BITABLE_APP_TOKEN" \
  --create-table
```

命令会创建 `ZaoFu Issues` Grid 和 `ZaoFu Issue Gantt` 视图。时间轴字段
`Start Time` / `End Time` / `Created At` / `Updated At` / `Synced At` 使用
Feishu `datetime` 字段；如果当前 lark-cli 版本或应用权限不支持 `gantt` 视图，
命令会报告初始化失败。确认权限或升级 CLI 后重试；只需同步记录和字段时可加
`--no-ensure-views`。

长报告正文仍通过 Feishu 卡片/Web 详情查看，表格只保存摘要、状态、引用和
生命周期字段。

如果只想写记录和字段、不自动创建或调整视图,使用:

```bash
uv run zf feishu sync-issue-table --backend lark-cli --no-ensure-views
```

如果要保留飞书页面里手工调整过的视图布局,但仍希望补齐字段和缺失视图,使用:

```bash
uv run zf feishu sync-automation-insights-table --backend lark-cli --no-ensure-layouts
uv run zf feishu sync-kanban-table --backend lark-cli --no-ensure-layouts
```

如果 Kanban 远端记录被删,同步会按 `Task ID` 重新创建记录并修复本地 ledger。
如果 `.env` 指向的 Kanban 表或 Base 已被删除,`sync-kanban-table` 会创建新的
Base/Table、补字段、覆盖 `.env` 里的 `FEISHU_BITABLE_APP_TOKEN`、
`FEISHU_BITABLE_TABLE_ID` 和 `FEISHU_BITABLE_URL`,然后重试同步。
需要失败而不是自动重建时使用 `--no-recreate-missing`。

多维表格字段名可按现有表头覆盖:

```bash
uv run zf feishu sync-kanban-table \
  --backend lark-cli \
  --field task_id=任务ID \
  --field title=标题 \
  --field status=状态 \
  --field assigned_to=负责人
```

## 事件驱动 Kanban 闭环

小时级 cron 仍可作为独立全量同步,但低延迟路径可由 `zf start` 管理常驻
projector。启用后,它消费 `task.status_changed`,按 Task ID 合并连续变化,读取
TaskStore 最新状态后写入 Base:

```yaml
runtime:
  feishu_projection:
    enabled: true
    backend: lark-cli
    auto_create_target: false
    poll_interval_seconds: 2
    reconcile_interval_seconds: 3600
    include_archive_days: 30
    max_actions_per_tick: 20
```

一次运行或前台观察:

```bash
uv run zf feishu project-kanban --once --backend lark-cli
uv run zf feishu project-kanban --watch --backend lark-cli
uv run zf feishu project-kanban --once --reconcile --backend lark-cli
```

如果当前项目还没有 Kanban Base/Table，可显式允许 projector 首次创建:

```yaml
runtime:
  feishu_projection:
    enabled: true
    backend: lark-cli
    auto_create_target: true
    base_name: "ZaoFu Kanban - my-project"
    table_name: Kanban
    time_zone: Asia/Shanghai
```

此模式要求 `FEISHU_APP_ID`、`FEISHU_APP_SECRET`（或 tenant token）以及
`FEISHU_FOLDER_TOKEN`。也可以只执行一次:

```bash
uv run zf feishu project-kanban \
  --once \
  --create-target-if-missing \
  --folder-token "$FEISHU_FOLDER_TOKEN"
```

创建流程会补齐字段、Grid/Kanban 视图和推荐布局，然后把非 secret 的
`app_token` / `table_id` 原子写入:

```text
<project.state_dir>/integrations/feishu/kanban-target.json
```

该项目级目标优先于进程中继承的全局 `FEISHU_BITABLE_*`，避免多个项目写入同一
张表。首次创建带单写锁；如果字段或视图初始化中断，下次启动会继续补齐已有
Base/Table，不会重复创建。`auto_create_target` 默认关闭，且仅处理“本项目尚无
目标”的初始化；远端 Base/Table 被人工删除时仍会 fail closed，由操作者清理该
项目目标记录后重新初始化，避免静默改投新资源。

`zf start`/`zf stop` 管理 sidecar 生命周期。cursor 位于
`project.state_dir/integrations/feishu/kanban-projector-cursor.json`;
日志位于 `project.state_dir/logs/feishu-kanban-projector.log`。失败会保留
pending 并持久退避,不会回滚 canonical TaskStore 状态。cursor 损坏、事件日志
截断或 reconcile 周期到期时执行全量收敛。一个项目同一时刻只允许一个 projector
owner。

当本地 sync ledger 丢失时,同步会先按 `Task ID` / `Row Key` 搜索远端并验证
精确相等:唯一命中会修复 ledger,零命中才创建,多个精确命中会 fail closed。

## Cron

生成推荐 crontab:

```bash
uv run zf feishu cron-template --daily-time 09:00 --hourly-minute 5
```

默认策略:

- Automation 每天同步一次。
- Automation Insights 每天同步一次。
- Kanbanboard 每小时同步一次。
- 生成命令固定使用 `--backend lark-cli`。
- 日志写到 `project.state_dir/logs/feishu-automation-sync.log` 和 `project.state_dir/logs/feishu-kanban-sync.log`。

安装时把输出粘贴到 `crontab -e`。cron 运行目录会固定到当前项目根目录,并显式带 `--state-dir`,不会误写到 `$PWD/.zf`。
# Settings 中的 /issue-merge 接入

Web Settings 的“飞书 Issue-merge”页只配置这一条流程，不会改动 task、Kanban
Agent 或 Run Manager 的接入。填写飞书 App ID、App Secret、Issue-merge 群
Chat ID、操作者 Open ID，以及 Issue Base 的 app_token/table_id；保存后密钥只
写入项目 `.env`，接口不会回显。保存会自动生成按钮回调签名密钥，并开启
`zf start` 管理的 Feishu inbound bridge。

“测试连接”只执行只读检查：租户凭证、机器人信息、目标群和 Bitable 字段读取。
飞书开发者后台的事件订阅、机器人入群及 Base 权限仍需由项目管理员配置。
配置生效后重启 `zf start`（或现有 bridge）；Issue Bitable 同步进程也应重新加载
项目环境变量。
