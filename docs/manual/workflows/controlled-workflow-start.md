# 受控 Workflow 启动

[English](controlled-workflow-start.en.md) · [工作流索引](README.md)

> 一个 Task-bound Workflow 产品能力，跨 Web、Kanban Agent、Channel、Feishu、CLI
> 复用同一 route catalog、proposal 和 approval 合同。

## 前置条件

启动 Workflow 前必须存在：

- 一个真实、可追踪 Task；
- 当前 Project 的 `zf.yaml` 和 active route catalog；
- 能说明 objective、输入和预期输出的 Task/PRD/issue/refactor 来源；
- 对应 route 所需的 artifact、profile、role 和 provider 能力；
- operator 授权，只有真正 apply 时需要。

Project、Channel 或一段聊天本身都不是 Task。

## 查询可用 Route

```bash
zf workflow routes --task TASK-ID --format json
```

Route 只能来自当前 Project 的 active catalog。Planner 可以根据需求推荐 route 和参数，
但不能从聊天文本发明 stage、role、writer 或 Kernel primitive。

常见 family 包括 PRD、Issue、Refactor、Research，以及已注册的 Generic Workflow。
具体可用项以当前命令输出为准。

## Preview、Propose、Apply

只读预览：

```bash
zf workflow start \
  --task TASK-ID \
  --route ROUTE-ID \
  --objective "本次运行的明确目标" \
  --parameters-json '{"expected_output":"verified delivery"}' \
  --preview \
  --format json
```

生成 durable proposal：

```bash
zf workflow start \
  --task TASK-ID \
  --route ROUTE-ID \
  --objective "本次运行的明确目标" \
  --parameters-json '{"expected_output":"verified delivery"}' \
  --propose \
  --format json
```

Operator 审核 exact proposal 后 apply：

```bash
zf workflow start \
  --proposal-event-id EVENT-ID \
  --authorization-ref APPROVAL-REF \
  --authorization-token "$ZF_WORKFLOW_ACTION_TOKEN" \
  --apply \
  --format json
```

Provider Agent 不得读取或接收 `ZF_WORKFLOW_ACTION_TOKEN`。

Apply 成功后自动拉起当前 Project 的 `zf start`，已有运行器会复用。
CLI、Kanban Agent 与 Channel Group 共用此行为；Preview/Propose 不拉起运行器。
响应 `runtime_start.status=running` 表示 watcher 已就绪，可以接收派发事件。
启动失败或超时会返回诊断和 `<state_dir>/logs/workflow-runtime-start.log` 路径；
修复后重试原提案即可，已提交 Request 和 invoke 会复用。

## Web/Kanban Agent 路径

```text
existing Task
  -> Kanban Agent clarifies objective and inputs
  -> active route options
  -> user selects, Chat about, or Customize
  -> Plan fixes the exact option
  -> independent Approve card
  -> Start workflow
  -> workflow.invoke.requested
```

![Task-bound Workflow 从计划、选择到独立批准和点火](../assets/quickstart-direct-workflow.webp)

Plan 不等于 Approve。`Continue` 或选中 route 只生成 exact proposal；执行外部副作用仍
需要独立授权。

## 动态 Workflow 的当前边界

当前已支持：

- Requirement 到 immutable proposal/effective config/Run 的受控合成；
- PRD、Issue、Refactor、Research 等注册 route；
- Generic Workflow static-safe v1，对已注册、安全 primitive 做 DAG/barrier/artifact completion；
- opt-in adaptive Research root 的有界、只读 provider-native 子任务；
- Run 内通过 replan/proposal 演进 scope、Task、AC 或下一步。

当前不能宣称：

- 任意 Agent 代码可热插入 Kernel；
- active Run 可以随意 hot-reload writer topology；
- 任意动态 writer 或 partial checkpoint 已成为默认；
- provider-native child graph 可以绕过根 TaskContract、Verify 或 completion gate。

当项目语义需要变化时，Agent 产出新的 plan/artifact/proposal；Kernel 只负责 schema、
identity、准入、currentness、权限、replay 和副作用。

## 观察与故障

```bash
zf workflow inspect
zf workflow audit
zf workflow gates
zf task trace TASK-ID
zf events --last 80
```

如果 start 被拒绝，优先检查：

- Task 是否真实存在且 current；
- route 是否 active；
- proposal event 是否与 Task、route 和参数一致；
- approval ref/token 是否只用于 exact proposal；
- required input refs 和 provider capability 是否齐全；
- Project 是否已有不允许并发的 active Run；
- workflow admission 是否留下明确 diagnostics。

## 完成定义

Workflow Start 成功表示 exact proposal 已获授权，产生了与同一 Task/route/参数绑定的
`workflow.invoke.requested`，且项目运行器已就绪。它不表示代码交付完成；后续结果由
Run、Verify、Closure 和 Completion Gate 判断。

## 相关

- [Project、Bootstrap 与 Workflow 点火](../20-project-bootstrap-workflow-ignition.md)
- [Plan、Task Map 与调度](../13-plan-task-map-orchestrator-dispatch.md)
- [观察一次交付](../operations/observe-delivery.md)
