# 08 创建 Task、Assignment Intent 与 Agent 协作

ZaoFu 当前不使用独立的 `New Task` 表单直接创建并派发 Worker。可追踪 Task 由
Kanban Agent 或 Channel 中的 PRD 结论生成 exact `Create Task` proposal，经过人工确认
后，通过受控 action 写入当前 Project。创建、分配意图和运行态派发是三个独立步骤。

## 创建可追踪 Task

1. 在 Web 左侧选择目标 Project。
2. 打开 Kanban Agent，说明目标、范围、验收标准和优先级；也可以先在 Channel Group
   中讨论，再从确认的 PRD 选择 `Create Task from PRD`。
3. 信息不完整时继续对话，不要确认一个范围含糊的 proposal。
4. 核对 `Create Task` proposal 中的 title、objective、acceptance criteria 和 priority，
   然后人工确认创建。
5. 回到 `Tasks` Board，按 Task ID 打开详情。

确认 proposal 只会通过受控 `create-task` action 更新当前 Project 的 TaskStore 和事件账本，
不会因为聊天文本、Channel 结论或页面选择而直接启动 Agent。

### 在 Kanban Agent 中附加文件

输入框左下角的 `+` 可以选择本地图片、日志、Markdown、JSON、Office/PDF 等文件；截图也可
直接粘贴到输入框。发送前会显示可删除的附件卡片。单个文件上限 20 MB，每条消息最多 5 个、
合计不超过 100 MB。

普通对话先把附件写为当前 Project 下的受校验 sidecar，事件只保存引用和 digest；Codex 对图片
使用原生图像输入，其他 Provider 可按提示中的本地只读路径检查文件。`/issue <描述>` 会把同一批
附件写入 Self-Issue Draft，后续仍由用户决定是否公开发布。

## 从一个 Task 读回完整上下文

Task 详情将长期任务需要的状态分成四个视图：

- `Summary`：当前 contract、依赖、assignee、handoff 和 assignment intent；
- `Activity`：事件时间线、当前 route、运行 DAG 和等待原因；
- `Evidence`：artifact ledger、最终 Task Map、Git refs 和测试证据；
- `Advanced`：attempt/session、provider、context、skills 和更细的运行信息。

`Agents` 页面补充展示 Worker health、context/token、provider、cost 和当前 Task。操作者应以
Task/Run/attempt identity 关联两边信息，而不是只凭 Agent 名称判断执行状态。

![Task 的上下文、活动、证据与 Agent 资源动态演示](assets/task-context-handoff.webp)

## 提出 Assignment Intent

在 Task 的 `Summary` 中找到 `Assignment Intent`，填写需要变更的字段：

- `Role`：期望承担工作的角色或实例；
- `Backend`：Codex、Claude Code 等已配置 backend；
- `Channel`：需要关联的 Channel Group；
- `Supervisor`：期望的监督入口；
- `Reason`：本次分配意图的原因。

点击 `Propose Assignment` 后，Web 追加 `assignment.intent.proposed`：

```json
{
  "type": "assignment.intent.proposed",
  "payload": {
    "task_id": "TASK-...",
    "role": "dev-ui",
    "backend": "codex",
    "channel_id": "",
    "supervisor": "",
    "reason": "operator assignment intent",
    "dispatches": false
  }
}
```

`dispatches=false` 是硬约束。这条事件记录可审计的分配意图，不改变当前 Worker，也不等于
`task.dispatched`。真正启动工作仍需经过已批准的 Task-bound Workflow 或 Kernel 控制的
dispatch action。

## Agent、Kanban Agent 与 Channel Group

- `Agent` 是执行代码、测试、评审或研究工作的 Worker；其输出必须形成 artifact、evidence
  或受控 action 请求。
- `Kanban Agent` 是 Project 内的通用 Coding Agent 和操作入口；它澄清需求、生成 proposal，
  但不绕过确认和 Kernel 状态机。
- `Channel Group` 让人和多个角色 Agent 围绕模糊问题讨论并收敛 PRD；对话正文是协作上下文，
  不是 Task truth。

推荐协作顺序是：讨论与澄清 -> exact Task proposal -> 人工确认 -> Task contract ->
assignment/workflow proposal -> 受控批准 -> Kernel 派发 -> evidence 回写。

## 验证要点

- Project A 创建的 Task 或 assignment intent 不能出现在 Project B 的 state dir。
- 未经人工确认的 `Create Task` proposal 不应产生 canonical Task。
- `assignment.intent.proposed` 必须通过 schema 校验、保留原始 request，并固定
  `dispatches=false`。
- 提出 Assignment Intent 后，不应出现由该 proposal 直接产生的 `task.dispatched`。
- Task 的 Summary、Activity、Evidence、Advanced 应能读回同一 Task/Run 的 current facts。

真实 provider smoke 优先使用临时 Project 和临时 state dir。没有可用 provider 时，至少验证
Web/API action、事件 schema、Project 隔离和无直接 dispatch 不变量。
