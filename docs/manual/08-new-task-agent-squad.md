# 08 New Task、Agent 与 Squad 使用手册

ZaoFu 的 Web `New Task` 是 Project-scoped work request 入口。它提供
Project 选择、issue/task contract 填写、assignee 选择三段式 work intake
体验,但保持 ZaoFu 的内核边界:创建任务只写入当前 Project 的 `TaskStore`,
选择 agent 或 squad 只记录 `assignment.intent.proposed`,不会直接启动
headless worker。

## 创建 Issue / Task

1. 先在 Web 左侧选择目标 Project。
2. 打开 Board 的 `New Task`,填写 title、behavior、verification。
3. 选择优先级和 assignee:
   - `Unassigned`: 只进入 Kanban,等待后续人工或自动 triage。
   - `Agent`: 指定一个当前 Project projection 中的 headless agent 实例。
   - `Squad`: 指定一个 channel-backed squad,由后续 routing/owner 决定拆分。
4. 点击 `Create Task`。

提交后会先执行 `create-task`。如果选择了 agent 或 squad,Web 会紧接着提交
`assignment-propose`,写入同一个 Project 的 `events.jsonl`:

```json
{
  "type": "assignment.intent.proposed",
  "payload": {
    "task_id": "TASK-...",
    "assignee_type": "agent|squad",
    "assignee_id": "...",
    "assignee_label": "...",
    "dispatches": false
  }
}
```

`dispatches=false` 是硬约束:这条事件表达意图和审计线索,不是运行态派发。

## Agent、Kanban Agent、Squad

- `Agent` 是可被 assignment intent 指向的 headless worker 实例,例如 Claude
  Code 或 Codex headless 进程。它不是新的控制面。
- `Kanban Agent` 是 Web/API 上的受控操作入口,负责把 operator 意图转换为
  `create-task`、`update-task`、`assignment-propose` 等 deterministic actions。
- `Squad` 在当前实现里映射为 channel-backed group。New Task 记录 squad
  intent 后,后续由 channel owner、routing 或人工 operator 决定是否拆子任务、
  分给哪个 agent、何时启动 headless worker。

这三者的协作关系是:Kanban Agent 接收操作 -> kernel 追加事件/更新任务 ->
Agent 或 Squad 通过后续受控运行路径消费这些事实。不要让 Web picker 直接写
worker session、workdir、progress 或 memory。

## ZaoFu 边界决策

- 保留:work-intake 的三段式体验,即选择 Project、填写 issue/task contract、
  选择 assignee。
- 保留:assignee 使用 `assignee_type + assignee_id + assignee_label` 表达,
  让 operator 能区分未分配、agent 和 squad。
- 约束:直接 agent queue 在 ZaoFu 中改成
  `assignment.intent.proposed`,由 kernel 后续受控路径决定是否 dispatch。
- 约束:squad assignment 在 ZaoFu 中映射为 channel-backed group intent,leader 或
  supervisor 是协调入口,不是新的调度器。
- 不做:不新增 workspace-global issue table,不在 Web picker 里直接启动 worker,
  不让 channel/squad transcript 成为 task truth。

## 验证要点

- Project A 创建的 assignment intent 不能出现在 Project B 的 state dir。
- Squad assignee 不应把 `TaskStore.assigned_to` 写成 channel id。
- Agent assignee 可以设置 `TaskStore.assigned_to`,但仍不能产生 `task.dispatched`。
- `assignment.intent.proposed` 必须通过 schema 校验,并保留原始 request 便于审计。

真实 provider smoke 时,优先使用临时 Project 和临时 state dir。没有 Claude Code
或 Codex headless 环境时,至少验证 Web/API action、事件、schema 和无直接 dispatch
不变量。
