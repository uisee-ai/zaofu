# Channel 到 PRD

[English](channel-to-prd.en.md) · [工作流索引](README.md)

> 当前合同：新建 Channel 默认采用自由讨论 `libre_discuss`。建群后保持安静，不带讨论 seed，
> 不自动发起讨论；由用户开始聊天并决定何时生成 PRD。已有 Channel 和三种旧模式保留。
> 生成或确认 PRD 不会自动创建 Task 或启动 Workflow。

## 什么时候使用

适合：

- 原始需求存在多个解释，需要 Owner、产品、架构、安全或质疑视角共同澄清；
- 结论需要跨 Web、飞书和 Kanban Agent 精确回到原始发起入口；
- 希望先形成 canonical PRD，再决定是否进入软件交付。

不适合：

- 已经清晰且只需一个 Agent 在当前 session 内处理的小问题；
- 已有 Task，只需要选择 Workflow；
- 用群聊成员直接修改 Project 或绕过审批点火。

## 产品模式

| 模式 | 何时使用 | 行为 |
|---|---|---|
| `libre_discuss` | 新建默认；按用户节奏讨论需求 | 无提及由主持人接话；可 @单人/@all；用户手动生成 PRD |
| `conversation` | 显式选择旧版自然讨论和定向 @mention | 用户或 Leader 决定谁继续回答，不自动 fanout |
| `clarification` | 需求缺字段、边界或 owner 决策 | 围绕开放问题逐项澄清 |
| `multi_lens` | 明确需要多个独立视角和综合 | 显式 Discuss 后执行有界 fanout/synthesis |

底层兼容名如 `manual_mention`、`mention_relay`、`fanout_then_synthesis` 是 engine
映射，不应作为三个并列的新产品模式展示。

## 创建 Channel

可以从 Kanban Agent 请求：

```text
为登录安全改造创建一个 PRD 讨论 Channel。
创建一个安静的自由讨论群，邀请产品、架构和安全视角。进群后由我开始说，PRD 也由我手动生成。
```

审核 Channel setup Plan 中的：

- template、Channel name 和 Owner；
- required/optional members；
- provider/model override；
- discussion mode 和预算；
- Leader、权限和允许的 handoff；
- source/origin receipt 目标。

选择创建后，系统仅创建 Channel、成员、skills 和权限。自由讨论不会把建群指令或项目简介
悄悄作为首条需求发送；项目简介和其他原材料可以在生成 PRD 时显式选入。

## 自由讨论与手动生成

- 普通消息由主持人接话；`@单人` 只请该成员回答；`@all` 请当前有效成员参加本轮。
- 主持人可在 `@all` 后组织一次必要的定向接力，不会持续广播、自动长总结或催促确认。
- 一键创建 profile、手动邀请和主持人提出邀请建议均保留。批准入群不自动让新成员发言；
  之后可点名他，或请他参与下一次 `@all`。
- 同轮至少两个 Agent 实际回复且至少两个有效问题时，先整理去重，再呈现题卡。
  不同场景、不同能力、独立子问和真实分歧必须保留；失败时原题标为未去重，仍可回答。
- 答完题后继续聊天，不会自动生成 PRD；未答题也不会锁住生成入口。
- 随时打开“生成 / 查看 PRD”，选择材料、项目简介或补充说明，再主动生成。
- 本次采用受理时固定的聊天、答案及材料。后续回复和补充留给下一版，生成期间仍可聊天。
- 停止只针对本次产物，不关闭群或删除旧 PRD；“重试本次”保留原材料，“生成新版”采用最新内容。

```text
创建静群 → 用户自由聊天 → 按需答题和补充
                         ↓ 用户主动生成
                      PRD 草稿 → 评估、修订、确认
```

旧模式继续沿用其既有讨论与综合规则。下面的历史演示展示旧讨论流程，不代表自由讨论的自动行为。

![旧模式中多角色讨论和收敛的流程](../assets/quickstart-channel-discussion.webp)

只有 Owner 确认的 revision 才能作为经过确认的 PRD。某个 Agent 的总结、整理完成或
多数成员口头同意不能替代评估、Owner authority 或后续开发授权。

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
