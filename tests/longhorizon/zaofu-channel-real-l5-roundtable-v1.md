# L5 真实化场景 — 4-Agent Mini Roundtable (zaofu-channel-real-l5-roundtable-v1)

目标:在 cj-mono mixed backend 之上,跑**同一 channel 同一 thread**里
**四个顺序回合**的圆桌互动,验:

1. arch (claude-code) 出最小设计
2. critic (codex) 评审 arch 设计、指出问题
3. dev (claude-code) 出 1 行函数体
4. reviewer (codex) 审 dev 代码

跟 L4-pair 差别:**4 轮 + 4 个 member,且 2cc/2cdx 混合背书**;跟 L5
epic 设计稿 (`zaofu-channel-real-l5-v1.md`) 差别:**仍不引入
fanout / exclusive_files / workflow.invoke 链**,只测圆桌轮转。

## 前置条件

同 L4 (`zaofu-channel-real-l4-v1.md` §前置条件)。

## Channel / Thread 标识

- `channel_id`:`ch-l5-rt-{utc-yyyymmdd-hhmm}`
- `thread_id`:`thr-main`
- `source`:`real-l5-runner`

## 成员

| member_id      | persona            | backend     |
| -------------- | ------------------ | ----------- |
| op             | operator           | -           |
| arch-cc-1      | arch (claude-code) | claude-code |
| critic-cdx-1   | critic (codex)     | codex       |
| dev-cc-1       | dev (claude-code)  | claude-code |
| review-cdx-1   | review (codex)     | codex       |

## 种子事件 (Setup)

| # | event.type                       | payload 关键字段                                                       |
| - | -------------------------------- | ---------------------------------------------------------------------- |
| 1 | `channel.created`                | channel_id, name=l5-roundtable-real                                    |
| 2 | `channel.member.added`           | member_id=`arch-cc-1`, backend=claude-code                             |
| 3 | `channel.member.added`           | member_id=`critic-cdx-1`, backend=codex                                |
| 4 | `channel.member.added`           | member_id=`dev-cc-1`, backend=claude-code                              |
| 5 | `channel.member.added`           | member_id=`review-cdx-1`, backend=codex                                |

## 圆桌四轮 (sequential)

| Round | target          | text 概要                                                                                  |
| ----- | --------------- | ------------------------------------------------------------------------------------------ |
| 1     | `arch-cc-1`     | 设计最小 Python `add(a, b)` 模块结构(模块名 + 函数签名 + 一个边界值,2 句以内,中文)。 |
| 2     | `critic-cdx-1`  | 评审上面 arch 设计,指 1 个最严重不足(2 句以内,中文)。                                  |
| 3     | `dev-cc-1`      | 写 `def add(a, b) -> int`,1 行函数体。                                                    |
| 4     | `review-cdx-1`  | 审查上面 dev 的代码(2 句以内,中文)。                                                   |

每 round wait:`reply.started → message.posted(role=assistant) → reply.completed`
(target_member_id 即当轮 member)。下一轮只在上一轮 reply.completed
落地后才发,用 fence timestamp (`after_ts`) 防止前轮 reply 满足后轮 wait。

## 成功判据

四个 round 各自的 reply 链都在每 round budget 内完整跑完 → `pass`。
任一 round 超时 / fail → 整体 fail,note 标 `roundN`。

## 预算

- 总 wall-clock:2400s (40 min)
- 每 round 子预算:600s
- 实际预期 ≤ $6

## 结果记录

`results.tsv` 一行,note 形如
`l5-roundtable-channel-real: ch-l5-rt-xxx rounds=4/4`。

## 与 L4 / L5-epic 关系

L5-roundtable 是 L4-pair → L5-epic 的台阶:多 1 个 backend 混合 +
多 2 轮但不引入 fanout / exclusive_files。L5-roundtable 连续 3 次
pass 才放心碰 L5-epic (`zaofu-channel-real-l5-v1.md`)。
