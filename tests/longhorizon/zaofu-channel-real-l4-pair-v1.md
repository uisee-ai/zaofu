# L4-pair 真实化场景 — 顺序双轮 Pair (zaofu-channel-real-l4-pair-v1)

目标:在 cj-mono mixed backend 之上,跑**同一 channel 同一 thread**
里的**两个顺序回合**,验:

1. dev (claude-code) 真出活
2. operator 紧接着 @ reviewer (codex),reviewer 真看见上一轮 dev reply
   并出审查回复
3. 两个 backend 不抢消息,thread_id 共享,member 状态在两轮间持久

跟 L4 (单轮) 差别:**多一轮 reviewer 互动**;跟 L5 (4-agent epic) 差
别:**仍只 2 个 member,不引入 fanout/exclusive_files 复杂度**。

## 前置条件

同 L4 (`zaofu-channel-real-l4-v1.md` §前置条件)。

## Channel / Thread 标识

- `channel_id`:`ch-l4-pair-{utc-yyyymmdd-hhmm}`
- `thread_id`:`thr-main`
- `source`:`real-l4-runner`

## 成员

| member_id      | persona            | backend     |
| -------------- | ------------------ | ----------- |
| op             | operator           | -           |
| dev-cc-1       | dev (claude-code)  | claude-code |
| review-cdx-1   | review (codex)     | codex       |

## 种子事件 (Round 1)

| # | event.type                       | payload 关键字段                                                                |
| - | -------------------------------- | ------------------------------------------------------------------------------- |
| 1 | `channel.created`                | channel_id, name=l4-pair-real                                                   |
| 2 | `channel.member.added`           | member_id=`dev-cc-1`, backend=claude-code                                       |
| 3 | `channel.member.added`           | member_id=`review-cdx-1`, backend=codex                                         |
| 4 | `channel.message.posted`         | mentions=[`dev-cc-1`], text="@dev-cc-1 请用 Python 写 def add(a,b) …简短" |

Round 1 wait:`reply.started → message.posted(role=assistant) → reply.completed`
(target_member_id=dev-cc-1)。

## 种子事件 (Round 2)

Round 1 `reply.completed` 落地后 runner 才发 Round 2:

| # | event.type                       | payload 关键字段                                                                |
| - | -------------------------------- | ------------------------------------------------------------------------------- |
| 5 | `channel.message.posted`         | mentions=[`review-cdx-1`], text="@review-cdx-1 请审查上面 dev 的代码,简短回复" |

Round 2 wait:同 Round 1,但 target=review-cdx-1。

## 成功判据

两个 round 各自的 reply 链都在 budget 内完整跑完 → `pass`。
任一 round 超时 / fail → 整体 fail,note 标 `round1` 或 `round2`。

## 预算

- 总 wall-clock:1200s (20 min)
- 每 round 子预算:600s
- 实际预期 ≤ $3

## 结果记录

`results.tsv` 一行,note 形如
`l4-pair-channel-real: ch-l4-pair-xxx round1=pass round2=pass`。

## 与 L4 / L5 关系

L4-pair 是 L4 → L5 的桥梁:验证多轮交互在同 channel 工作,但不引入
exclusive_files / fanout 复杂度。L4-pair 连续 3 次 pass 才放心碰 L5。
