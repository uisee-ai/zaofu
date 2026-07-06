---
name: zf-channel-discussion-synthesizer
description: "Use when you are the synthesizer (usually product_pm) of a ZaoFu channel requirement-clarification discussion (doc 122). Defines question dedup, the clarified-requirement artifact format, and the consensus proposal flow."
---

# Channel 需求澄清讨论 — Synthesizer 协议

你除了自己的视角发言(见 participant 协议),还负责两件只有你做的事:**合并重复问题**和**收敛成稿**。

## Phase 2 开场:台账去重

盲答后三个视角的问题必有重叠。逐对合并(保留问得更锋利的那个):

```bash
zf emit channel.question.merged --actor <你的member_id> --payload '{"channel_id":"<CH>","thread_id":"main","question_id":"<被合并的q>","into_question_id":"<保留的q>"}'
```

## 收到 synthesis 请求时(channel.synthesis.requested 指向你)

台账已清零。产出**澄清需求 artifact**:

1. 写文件到 `.zf/channel-artifacts/clarified-<slug>.md`,结构固定:

```markdown
# 澄清需求:<标题>
## Decisions(逐条:问题 → owner 的回答)
## Assumptions(显式假设 + 风险)
## Out of Scope(明确不做)
## Acceptance Criteria(EARS 句式:When <触发>, the <系统> shall <行为>)
```

只写台账里有据的内容——**每条 Decision 必须能对应一个 resolved question**,不发明 owner 没说过的决定。

2. 在 channel 回复里贴出稿件要点,然后提案:

```bash
zf emit channel.consensus.proposed --actor <你的member_id> --payload '{"channel_id":"<CH>","thread_id":"main","artifact_ref":".zf/channel-artifacts/clarified-<slug>.md","proposed_by":"<你的member_id>"}'
```

3. 等其余角色 `signed`;出现 `blocked` → 讨论自动重开,blocker 进台账 → owner 答完后**修订稿件、重新 proposed**(新提案会重置签名)。

## 你签自己的稿

提案后同样 emit 你自己的 `channel.consensus.signed`。全角色签 + owner 确认后,kernel 会自动收敛(closed + idea-to-product 提案),**不需要你做任何 workflow 动作**。
