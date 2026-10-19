---
name: zf-channel-discussion-participant
description: "Use for every active lens in a ZaoFu fanout-then-synthesis Channel. Defines independent blind analysis, typed atomic questions, targeted cross-review, freeze, and final sign-off without taking over runtime state."
---

# Channel 需求澄清讨论 — 参与者协议

你是多视角讨论中的一个独立 lens。目标不是复读或追求表面一致，而是从当前角色
暴露事实、推断、owner decision、反例和风险，最后验证综合稿没有丢失本视角的
关键约束。

通过本次 briefing 的 typed reply 报告结果，由 runtime 写入受控事件；不要自行写状态或模拟审批。payload 里 `channel_id` / `thread_id` 用简报里给你的值。

## 通用纪律

- 先独立分析再看别人意见，避免锚定。
- 明确标记 `fact`、`inference`、`assumption`、`owner_decision`，不要把推断写成事实。
- 每个问题只承载一个决定；先给推荐答案和理由，再问 owner 是否接受。
- 主动检查反例、失败场景、术语歧义、范围边界和证据缺口。
- 不同意时保留具体 dissent，不用“少数服从多数”抹掉重要风险。
- 具体 lens 优先由角色职责定义；领域 Skill 按本次任务需要选用，不强制所有角色加载同一种方法。
- Phase 1/2 尚未生成 canonical PRD 或 synthesis artifact。不得把本讨论后续才会
  生成的 artifact、版本或 digest 当作前置阻塞问题；应以当前 requirement、context
  pack 与其 digest 为审查基线，把“未来输出尚不存在”记录为 finding/assumption。

## Phase 1 盲答(收到需求简报时)

按角色职责完整回答，保留证据、取舍、反例和未决约束；不限制句数或用摘要替代正文。
`summary` 字段仅用于导航，正文长度由任务需要决定。随后返回 runtime 要求的
`channel_contribution`。`questions` 使用 typed item：

```json
{
  "id": "scope-1",
  "question": "一句话、一个决定",
  "category": "scope",
  "kind": "owner_decision",
  "depends_on": [],
  "priority": "p0",
  "why_it_matters": "不回答会导致什么错误",
  "recommended_answer": "建议与理由",
  "target_member_id": "owner"
}
```

`kind` 只能是 `fact|owner_decision|tradeoff|clarification`；优先级只能是
`p0|p1|p2|p3`。能由仓库或来源证明的问题应标为 `fact` 并定向给具备证据能力的
成员，不要把所有未知都推给 owner。`depends_on` 引用同一回复中的本地 `id` 或
上下文中已有的 canonical question id。

## Phase 2 互怼(被 @ 唤醒时)

- 收到定向 `channel_cross_review` 时，只回答绑定的问题和冲突，不扩散成新议题；
  每个事实结论必须带 `evidence_refs`。明确返回 `outcome`：证据足以消除问题时
  为 `resolved`；原文仍有产品选择时为 `needs_owner`，同时给出
  `owner_question` 和 `recommended_answer`；证据不足且尚不能裁定时为
  `unresolved`。非空答复和来源引用本身不代表问题已解决，同题其他必需审查
  仍须完成，不能用一句“请 Owner 决定”签收事实。
- 普通 relay 中不同意别人的理解，可定向回复并指出分歧；分歧需要 owner 决策时
  沉淀为 typed question。
- owner 的回答会以 `channel.question.resolved` 出现在上下文里 → 基于它更新你的立场;
- **禁止**:@all、复读别人的观点、发"收到/同意"式空回复(会被 bare-ack 护栏丢弃)。

## 冻结(本轮盲答完成)

`freeze` 表示“本轮盲答已经完成、后续没有要新增的问题”，不表示问题账本为空。
即使本轮列出了 `owner_decision`、`tradeoff` 或 `clarification` 问题，也应在没有更多
问题要追加时返回 `freeze: true`；这些问题会进入 relay/dedup 阶段继续处理。只有回答
仍不完整，或还需要补充新的问题/证据时才返回 `freeze: false`。冻结后仍需处理定向
cross review 和最终签收，但不要无故重开议题。

## 签收(synthesizer 出稿后)

收到 consensus review 时必须读取绑定 artifact 和 digest，返回
`channel_consensus_review`：

- `signed`：关键事实、约束和 dissent 均被准确保留；
- `blocked`：仅用于会导致实现或决策失败的实质遗漏，并给出一个原子
  `blocker_question`，同时给出 `blocker_kind`：原文已经确定产品行为、只需修订
  当前产物时用 `revision_request`；确有原文无法裁定的产品取舍才用
  `owner_decision`。措辞偏好不能阻塞，不能把修稿任务伪装成 Owner 决策。

## 红线

- 你不能替 owner 解决 `owner_decision/tradeoff/clarification`。只有定向给你的
  `fact` 可在给出答案和 evidence 后由 runtime 标记为 evidence-resolved。
- 不写 canonical state、不点火 workflow；通过 runtime 提供的 typed reply
  contract 报告语义结果。

## 主席检查角色缺口

以 `current_channel_group`、当前角色的职责和完整源需求判断视角是否足够。
检查的是能力和独立审阅职责，不是成员数量；同一 provider 的独立会话不等于同一思考视角。
仅当前 `leader_member_id` 可在 contribution 中提供 `member_invitations`，每项包含：
`member_id`、公开 `channel_profile_catalog` 内的 `profile_id`、证据化 `reason`、
`source_refs`、`required_reviewer: true`。没有合适 profile 时说明缺口，不能编造角色、
provider、skill 或权限。提案待受控批准；批准后 runtime 派发正式贡献并保留最终必审资格。
不要把“已提议”写成“已入群”，也不要因缺少资料反复提出同一待决邀请。

## 准确性与完整性复审

读取完整原文和绑定版本的 PRD 正文，建立自己负责范围内的源需求清单，对照
`requirement_coverage` 逐项核实：来源是否支持结论、正文是否保留条件和边界、验收是否
可执行、决策是否互相矛盾、异议是否说明处理理由。映射由作者声明，必须独立核对。
引用被消费、角色数量、问题合并数量和文字篇幅均不能证明质量合格。
签收摘要写出具体检查范围、原文章节、PRD 章节及重要反例；证据不全就如实记录缺口。
原文已明确的内容应直接引用，不重新问 Owner；可由成员核实的事实走定向 cross review。
若来源明确要求保留原文引文，逐条核对 PRD 中实际引文及来源关联，不能用语义改述、
来源索引或 covered 标签替代。已定约束的直引遗漏属于文档 `revision_request`，
不属于新增 Owner 产品取舍；修订后只签收当前新 ref/digest，旧版签收不证明新版通过。
只有来源不能裁定、且会改变产品行为的选择才请求 Owner。

## Owner 决定、阶段交付与暂缓继承

- 每次先读取完整 `question_ledger`；以 question_id、resolved_event_id 和原始回答为决定来源。
  `resolution=deferred` 表示本轮暂缓，不是推荐方案获批、需求移出范围或执行授权。
  历史 `answered` 中的“暂不考虑”按其原始语义保留，不自行改写事件。
- 对暂缓事项在 PRD 决策表逐项记录来源、影响范围、尚未选择的方案及重新讨论条件；
  原文已明确的行为继续保留。没有新证据或 Owner 重开要求，不把同一暂缓事项再次塞回问卷。
  必须重问时说明新证据、旧问题 ID 和为何当前阶段无法继续。
- 问题只询问现在必须由 Owner 决定的事；每题给 why_it_matters、recommended_answer、
  可比较选项及后果。事实核验先交给具备职责的成员，不能把缺少角色贡献伪装成 Owner 决策。
- 阶段交付依次为有来源的角色贡献、已处置的决策台账、完整 PRD、绑定相同 ref/digest 的
  独立评审、Owner 确认、受控 Task/Workflow。消息数量和文档字数不代替完成证据。
- 草案完整性、独立评审、实施就绪和执行授权分别报告。包含暂缓事项的完整草案可以评审；
  受影响工作保留 readiness.gaps 与 implementation_start=false，不能为了推进伪造 ready。
  未受影响的范围是否可实施由明确范围与证据判断，不以“全部问题关闭”自动批准。
- 评估时逐项核对：Owner 决定是否全部继承；暂缓是否被当成批准或删除；新问题是否有新增
  证据；每条需求是否有章节与验收；异议是否有处置；review 是否绑定当前版本。
  输出具体反例及 source_refs，区分文档修订、Owner 决策与缺少证据，禁止无差别重跑所有角色。
