# Channel 到 PRD

[English](channel-to-prd.en.md) · [工作流索引](README.md)

> 当前合同：Channel 是面向人和 Agent 的需求讨论室。默认是自然 conversation；
> clarification 和 multi-lens 需要显式选择。创建 Channel 不等于自动 fanout，确认 PRD
> 也不等于创建 Task 或启动 Workflow。

## 什么时候使用

适合：

- 原始需求存在多个解释，需要 Owner、产品、架构、安全或质疑视角共同澄清；
- 结论需要跨 Web、飞书和 Kanban Agent 精确回到原始发起入口；
- 希望先形成 canonical PRD，再决定是否进入软件交付。

不适合：

- 已经清晰且只需一个 Agent 在当前 session 内处理的小问题；
- 已有 Task，只需要选择 Workflow；
- 用群聊成员直接修改 Project 或绕过审批点火。

## 三种产品模式

| 模式 | 何时使用 | 行为 |
|---|---|---|
| `conversation` | 默认；自然讨论和定向 @mention | 用户或 Leader 决定谁继续回答，不自动 fanout |
| `clarification` | 需求缺字段、边界或 owner 决策 | 围绕开放问题逐项澄清 |
| `multi_lens` | 明确需要多个独立视角和综合 | 显式 Discuss 后执行有界 fanout/synthesis |

底层兼容名如 `manual_mention`、`mention_relay`、`fanout_then_synthesis` 是 engine
映射，不应作为三个并列的新产品模式展示。

## 创建 Channel

可以从 Kanban Agent 请求：

```text
为登录安全改造创建一个 PRD 讨论 Channel。
先按自然 conversation 建群，邀请产品、架构和安全视角；不要自动创建 Task 或启动 Workflow。
```

审核 Channel setup Plan 中的：

- template、Channel name 和 Owner；
- required/optional members；
- provider/model override；
- discussion mode 和预算；
- Leader、权限和允许的 handoff；
- source/origin receipt 目标。

选择创建后，系统物化 Channel、Members、skills、权限和原始需求。默认
`conversation` 不会因为 Channel 创建完成就自动 fanout 所有成员。

## 讨论和收敛

典型路径：

```text
origin request
  -> durable message ACK
  -> natural conversation
  -> optional clarification
  -> optional explicit Discuss / multi_lens
  -> explicit Finalize
  -> PRD draft
  -> Continue | Revise | Owner confirm
  -> channel.consensus.reached(ref, digest, revision)
  -> exact-origin PRD receipt
```

![Channel 中从自然讨论到多角色收敛的动态过程](../assets/quickstart-channel-discussion.webp)

讨论中可以：

- @mention 一个成员回答具体问题；
- 补充约束并继续同一 thread；
- 显式触发多视角讨论；
- 自然对话结束后切换为 `multi_lens`，等待在途回复结束，再点击 `Start discussion`；
  该动作通过受控重启关闭旧讨论会话，保留消息并启动当前模式的独立多角色讨论；
- 查看成员 identity、provider、状态和权限；
- 要求 Finalize 形成 PRD draft；
- 继续、修订或由 Owner 确认某个 revision。

只有 Owner 确认的 revision 才是 canonical PRD。普通 synthesis、某个 Agent 的总结或
多数成员口头同意都不能替代 Owner authority。

## 从 PRD 进入交付

确认 PRD 后仍需独立交接：

```text
confirmed PRD
  -> existing Task
     or Create Task from PRD proposal -> human confirm
  -> Task-bound Workflow Plan
  -> exact Workflow proposal
  -> Approve
  -> Kernel starts Workflow
  -> read-only Task/Run/Delivery receipt back to the original Channel
```

只有 Channel 的 exact `leader_member_id` 且拥有 `propose_workflow` 权限，才能发起
Workflow handoff proposal。它仍不能批准自己的 proposal，也不能持有
`ZF_WORKFLOW_ACTION_TOKEN`。

## CLI 补充输入

稳定的低层消息命令是：

```bash
zf channel say CHANNEL-ID \
  --text "请补充失败场景，并由 @critic 复核。" \
  --member-id reviewer \
  --mention critic
```

Finalize、Owner confirm、成员权限和 Workflow handoff 当前主要通过 Web/Kanban Agent、
Feishu 或受控 action 入口完成，不应通过手写 `events.jsonl` 模拟。

## 观察与故障

重点检查：

- 原始消息是否仅入账一次，ACK/NACK 是否明确；
- 当前 product mode 是否为预期值；
- multi-lens 是否由显式动作启动；
- PRD ref、digest、revision 和 owner identity 是否齐全；
- receipt 是否回到 exact origin；
- Task/Workflow proposal 是否与 confirmed PRD revision 绑定；
- 非 Leader 或无权限成员的 handoff 是否被拒绝；
- provider 失败是否被去重并留下可恢复状态。

```bash
zf events --last 100
zf status --workers
```

## 完成定义

Channel-to-PRD 完成只表示：Owner 已确认一个有 ref/digest/revision 的 PRD，且来源收到
回执。只有随后创建真实 Task、批准 Workflow 并由 Kernel 执行，才进入交付闭环。

## 相关

- [15 Channel 深度使用手册](../15-channel-collaboration.md)
- [受控 Workflow 启动](controlled-workflow-start.md)
- [Feishu AI-Native Bridge](../19-feishu-ai-native-direct-bridge.md)

新建 Task 的交接默认使用 `prepare_workflow=true`：Task 确认后准备 Workflow YAML Preview，
再由 Owner 独立确认启动。可信项目目录在编译提案时绑定为 `target_root`；明确选择的目标目录保留，
不再只给旧的嵌套路由传目录。
