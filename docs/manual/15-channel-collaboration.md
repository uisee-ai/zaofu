# Channel 协作使用手册

[English](15-channel-collaboration.en.md) · [Channel 到 PRD 最短路径](workflows/channel-to-prd.md)

> 适用对象：希望让人和多个 Agent 围绕需求持续讨论、形成 Owner 确认 PRD，并在
> Web、Kanban Agent 或飞书中继续协作的操作者。
>
> 当前状态：按 2026-08-03 代码和测试核实。默认 Channel mode 是
> `conversation`；创建 Channel 不自动 fanout，
> `multi_lens` 必须显式启动。

## 1. 启动前检查

真实 Provider Channel 推荐通过可信本地 canonical launcher 启动：

```bash
tools/start-webkanban.sh --host 127.0.0.1 --port 8001
tools/start-webkanban.sh --port 8001 --status
```

共享或非可信主机必须使用普通 sandbox；可信本地绕过策略不等于每个 Channel Member
自动获得 Project 写权限。详见
[16 真实 Provider Preflight](16-real-codex-provider-preflight.md)。

## 2. Channel 是什么

```text
Channel
├── Origin / Owner / Leader
├── Members and provider bindings
├── Messages, threads, ACK/NACK
├── Discussion mode and explicit actions
├── PRD draft / revision / owner confirmation
└── Result and workflow receipts
```

Channel 是运行时动态对象，由 `channel.*` 事件、sanctioned sidecar 和 Channel contract
共同维护；不在 `zf.yaml` 里预声明一个静态 group。

Channel 与 Workflow 是两个状态机：

- 创建 Channel 不要求已有 Task；
- Channel 负责需求澄清、评审、决策和 PRD 定稿；
- Channel 不调度 Task、不修改代码、不决定交付 terminal；
- confirmed PRD 不自动创建 Task；
- 只有受控 Task/Workflow proposal 经人工批准后才进入 Kernel Workflow；
- Delivery receipt 只读返回 Channel，不让 Channel 接管运行态。

## 3. 三种产品模式

| Mode | 默认触发 | 适用场景 | Engine mapping |
|---|---|---|---|
| `conversation` | Channel 创建后默认 | 自然群聊、定向 @mention、持续讨论 | `manual_mention` |
| `clarification` | 显式选择或模板声明 | 逐项解决开放问题和缺失决策 | `mention_relay` |
| `multi_lens` | 显式 Discuss | 独立多视角、relay/critique 和 synthesis | `fanout_then_synthesis` |

Engine mapping 是兼容实现细节，不是额外产品 mode。`max_rounds` 约束显式有界讨论，
不会让 `conversation` 自动唤醒所有成员。

## 4. 内置模板

当前代码内置 5 个版本化模板，版本为 `2026-07-31.1`：

| Template | 适用场景 | Required members | 默认 mode | Leader / 默认回复者 | 默认预算上限 |
|---|---|---|---|---|---|
| `prd-clarification` | 收敛 PRD、范围、用户场景和验收标准 | `product_pm`、`arch`、`critic`、`synthesizer`；可选 `security_reviewer` | `conversation` | `product_pm` / `synthesizer` | 20 轮、并发 5 |
| `research-review` | 来源核验、证据分级和方案比较 | `researcher`、`arch`、`critic`、`synthesizer` | `conversation` | `researcher` / `synthesizer` | 16 轮、并发 4 |
| `architecture-review` | 架构、实现一致性、安全和候选门禁评审 | `arch`、`security_reviewer`、`dev_reviewer`、`critic` | `multi_lens` | `arch` / `arch` | 16 轮、并发 4 |
| `quick-change` | 范围明确的小功能或缺陷修复 | `tech_leader`、`dev_reviewer`、`qa_analyst` | `conversation` | `tech_leader` / `tech_leader` | 12 轮、并发 3 |
| `incident-triage` | 故障证据、影响、根因和恢复建议 | `tech_leader`、`qa_analyst`；可选 `security_reviewer` | `clarification` | `tech_leader` / `tech_leader` | 12 轮、并发 3 |

预算是默认上限，不要求跑满。Kanban Agent Plan 可通过 `budget.max_rounds`、
`max_parallel_replies` 和阶段 deadline 收紧具体请求。

模板固定 required role、skill refs、允许的 override 和 writer scope；override 不能新增
任意角色。创建前所有 skill ref 必须解析成功。物化时所有 Member 都以 `read_only`
permission profile/ceiling 启动，Leader 额外取得 `propose_workflow`；`writer_role` 和
`writer_scope` 只描述产物责任与受控写入边界，不自动授予文件写权限。

模板边界如下：

- `prd-clarification` 维护问题台账、Owner 澄清和 PRD/需求快照，不绕过 Create Task 与
  Workflow 审批；
- `research-review` 只做证据讨论，不隐式启动 Research Workflow；
- `architecture-review` 输出多视角结论和 Workflow 建议，不直接实施；
- `quick-change` 只适用于范围已经冻结的小改动；
- `incident-triage` 只诊断并建议 controlled action，恢复仍由 Kernel 或获批动作执行。

没有显式 mention 的消息发给默认回复者；显式 `@role` 始终优先。

## 5. 通过 Kanban Agent 创建

```text
为登录安全改造创建一个 PRD 讨论 Channel。
邀请产品、架构和安全视角；先用自然 conversation，不要自动创建 Task 或启动 Workflow。
```

Kanban Agent 返回 action-bound Channel setup Plan。审核：

- `template_id`、name、origin 和 Owner；
- required/optional members；
- provider/model override；
- product mode、budget 和 optional roles；
- Leader 和 `propose_workflow` authority；
- source receipt 目标。

Action-bound Channel setup Plan 的每个选项都必须显式绑定
`conversation|clarification|multi_lens`，不能静默继承模板默认值。确认界面同时展示
初始路由是单 responder、facilitated relay，还是 N 路 blind fanout；缺少 mode 的旧
pending Plan 在 apply 时 fail closed，已完成的历史 receipt 仍保持幂等可读。

点击 `Create & start` 会原子执行：

```text
channel-create-and-start
  -> 创建 Channel
  -> 物化 Members、skills、permissions 和 profile binding
  -> 投递去掉控制语句后的 clean business requirement 并写 durable ACK/NACK
  -> 初始化 product mode
  -> conversation 等待定向交互
```

它不会因为按钮名包含 `start` 就自动 fanout。只有 mode 本身是 `multi_lens`，或后续
显式 Discuss，才运行有界多视角讨论。

`Chat about` 会把补充内容送回同一 Kanban Agent session 并修订 Plan，不执行 option，
也不要求用户手改 JSON。

## 6. 自然讨论和多视角讨论

在 `conversation` 中：

- 人、Leader 或有权限 Member 发布消息；
- @mention 只定向请求需要的成员；
- thread 可以持续多轮，不需要重建 Channel；
- reply delta 通过 SSE 实时展示自然语言；尾部机器 contract 在服务端隔离，不会先以 JSON
  出现在聊天中；terminal body、typed contract 与 refs 分别持久化；
- provider 失败留下可诊断状态，不自动把所有成员重跑。

普通 Agent 回复以自然语言为主，不会在下方重复生成一份 findings 报告。只有 P0/P1 风险、
未解决冲突等可操作例外贴在对应回复下方；Owner 问题仍使用 AskUserQuestion，source、
evidence 和 artifact refs 通过折叠入口按需查看。机器 contract 保留在 canonical
projection/artifact 中，不作为聊天正文展示。

明确需要独立视角时执行 Discuss / `multi_lens`：

```text
phase1 blind answers
  -> phase2 relay / critique
  -> phase3 synthesis
```

这一流程受 round/member/budget 约束，并与普通 conversation history 关联。它是一次显式
讨论操作，不是 Channel 的永久默认状态。

普通 conversation 的 Provider context 使用有界历史摘要；`multi_lens` 的 cross-review
和 synthesis 会绑定此前各轮完整 message sidecar，并要求回复逐项声明已消费 digest。
缺正文、hash 不匹配、漏读来源或来源超预算时，该阶段会明确失败，不会用 excerpt 生成
“已完成”结论。Provider 因 max token/context/output length 停止时会在同一 session 最多
续写两次；可用 `ZF_CHANNEL_PROVIDER_MAX_CONTINUATIONS=0..4` 调整，耗尽后状态为
`incomplete`，不会进入 PRD/consensus。

完整正文只在目标 Provider dispatch 时从当前 context-pack sidecar 严格校验并加载；Channel
Web projection 只保留 manifest、ref、digest、计数和覆盖状态，不复制或返回正文数组。
内部 synthesis repair 指令在对话 projection 中只显示一条简短的 Kernel 状态，不展示
合同诊断或长 artifact 路径；完整诊断仍保存在审计 sidecar 和事件中。
重复生成 PRD revision 时，同一 thread 中问题文本仅做保守的 whitespace/case 规范化
去重并复用原 question identity；已回答的问题不会因为 revision 再次出现而重新打开，
新问题仍会进入 Owner decision frontier。

讨论收敛后 Channel 仍保持可交互；人可以继续追问、补充需求或显式重开多视角讨论，
不需要重建 Channel。若 projection 中存在 Owner 问题 frontier，Web 最多逐题展示当前
前三题：可枚举问题使用 2--3 个互斥选项和单推荐项，开放问题使用自由文本；提交后仍由
`channel.question.*` ledger 保存逐项 resolved fact，组件本身不成为第二套问题状态机。

若 active discussion 需要切换 mode 或从头复核，Details 中使用 `Restart discussion`。
该动作会先以 `cancelled / explicit_restart` 关闭旧 session，再以新的 trigger message
identity 启动当前 mode；原需求 message 仅通过 `source_requirement_message_id` 引用，
不会复用旧 message id 而被幂等入口误判为重复发送。

![Channel Group 中自然讨论、定向回复与多角色收敛](assets/quickstart-channel-discussion.webp)

## 7. PRD Finalize 和 Owner authority

```text
conversation / clarification / multi_lens
  -> explicit Finalize
  -> PRD draft artifact
  -> Continue | Revise | Owner confirm
  -> channel.consensus.reached(ref, digest, revision)
  -> exact-origin PRD receipt
```

必须满足：

- PRD body 完整保存在 sanctioned artifact/sidecar；
- readiness artifact 必须已经给出 `ready`、`needs_owner` 或 `needs_multi_lens` 结论；`unassessed` 的 PRD 不能由 Owner 确认；
- event 只保存 identity、revision、digest、preview/ref 和 causation；
- revision 更新使用 currentness/CAS，旧 revision 不能覆盖新确认结果；
- 只有 Owner confirm 能产生 canonical PRD；
- receipt 必须回到 exact origin，失败时保持可重试而不重复确认；
- synthesis、Agent 自述或多数意见不等于 Owner confirmation。

## 8. 从 PRD 交接到 Workflow

```text
confirmed PRD
  -> existing Task
     or Create Task proposal -> human confirm
  -> Task-bound Workflow Plan
  -> exact workflow-start proposal
  -> independent Approve
  -> Kernel Workflow
  -> Task/Run/Delivery receipt back to Channel
```

只有 exact `leader_member_id` 且拥有 `propose_workflow` permission 的 Member 可以创建
handoff proposal。它不能批准 proposal、读取 operator token 或直接 emit
`workflow.invoke.requested`。

完整点火见[受控 Workflow 启动](workflows/controlled-workflow-start.md)。

## 9. Channel 与 Research Workflow

`research-review` 是对已有材料的讨论模板，默认仍是 conversation。真正的 Research
Workflow 需要：

1. 真实 Task；
2. 用户明确要求 Research；
3. `zf workflow routes --task TASK-ID` 返回 active Research route；
4. Plan 选择 route；
5. 独立 Approve exact proposal。

Research 结果是可审计 artifact，不会自动变成 PRD Workflow。

## 10. CLI 消息入口

```bash
zf channel say CHANNEL-ID \
  --text "请补充失败场景，并由 @critic 复核。" \
  --member-id reviewer \
  --mention critic
```

该命令通过 `channel-post-message` ControlledAction 写入消息事实，不直接编辑
`events.jsonl`。Finalize、Owner confirm、成员权限和 Workflow handoff 当前由 Web、
Kanban Agent、Feishu 或其他 token-gated action 提供。

## 11. 飞书关联

飞书 chat 可路由到已有 Channel；入站消息保留 origin，出站 PRD/result receipt 精确返回
同一来源。Bridge 只发布 message/intent/ref 或请求 controlled action，不直接修改 Task、
Workflow 或 Run。配置和验证见
[19 Feishu AI-Native Bridge](19-feishu-ai-native-direct-bridge.md)。

## 12. 观测和故障定位

```bash
zf events --last 100
zf status --workers
```

重点检查：

- template digest、mode、Owner、Leader 和 roster；
- 原始需求是否只投递一次并有 ACK/NACK；
- conversation 是否没有非预期 fanout；
- Discuss operation 的成员、轮次、budget 和 terminal refs；
- PRD draft/confirmed revision、digest 和 exact-origin receipt；
- handoff 是否由 exact Leader 提案并经过独立 Approve；
- provider failure、retry 和 result receipt 是否幂等；
- required sidecar 缺失是否 fail closed。

## 完成定义

Channel 自身完成条件是可继续交互、Owner-confirmed PRD 可追溯、来源回执成功。软件交付
完成条件属于 Task/Workflow/Run/Delivery，不属于 Channel synthesis。
