# L4 真实化场景 — Pair-Programming Channel Thread (zaofu-channel-real-l4-v1)

目标:在 cj-mono 的 mixed (claude-code + codex) backend 之上,跑一个
**真实** LLM 的 Agent Channel 结对编程线程。Operator 发起一个 task,
拉一个 dev (claude-code) 一个 reviewer (codex) 进同一个 thread,看
``channel.message.posted → channel.agent.reply.requested →
channel.agent.reply.started → ... → channel.agent.reply.completed``
事件链是否在 wall-clock 预算内完整跑通。

跟 L3 的差别:**真 LLM、真 backend、真时钟**。跟 e2e 的差别:**只验
channel 链,不验整个 design→ship 流水线**,所以单次预算 < 10 min /
~$1。

## 前置条件 (preconditions)

- 工作目录:`/path/to/example-project`(本仓库外的一个真实项目)
- 配置:`zf.yaml`(默认 mixed backend preset)
- 状态目录:`.zf-mixed/`(必须已 `zf init` 过)
- 编排器:`zf start` 必须在另一个终端已经在跑;tmux session
  `zf-mixed` 存在
- CLI:`claude` + `codex` 都必须 login 完成,`tmux` 可用
- 预算:`global_budget_usd: 5000` 足够,但实际消耗预期 ≤ $2

runner 在 dry-run 模式下只**检查**这些前置,不写任何 cj-mono 文件;
在 live 模式下会真的 emit 事件。

## 角色 / 成员 (members)

| member_id      | persona            | backend     | role          |
| -------------- | ------------------ | ----------- | ------------- |
| op             | operator           | -           | 人类操作者    |
| dev-cc-1       | dev (claude-code)  | claude-code | 写代码        |
| review-cdx-1   | review (codex)     | codex       | code review   |

(成员 id 跟 cj-mono `zf-mixed.yaml` 的 role pool 对齐,prefix 与
`role:name` 一致。)

## Channel / Thread 标识

- `channel_id`:`ch-l4-pair-{utc-yyyymmdd-hhmm}`(runner 自动生成,
  避免与其它 channel 冲突)
- `thread_id`:`thr-main`(单线程,够用)
- `source`:`real-l4-runner`

## 种子事件序列 (seed events)

| # | event.type                       | actor           | 关键 payload 字段                                              |
| - | -------------------------------- | --------------- | -------------------------------------------------------------- |
| 1 | `channel.created`                | `op`            | channel_id, name, source, scope=`["src/lh_demo/"]`             |
| 2 | `channel.member.added`           | `op`            | channel_id, member_id=`dev-cc-1`, persona=`dev`, backend=cc    |
| 3 | `channel.member.added`           | `op`            | channel_id, member_id=`review-cdx-1`, persona=`review`, …      |
| 4 | `channel.message.posted`         | `op`            | text="写一个 add(a,b) 函数 + pytest,放 src/lh_demo/math.py" |
| 5 | `channel.agent.reply.requested`  | `op`            | target_member_id=`dev-cc-1`, message_id from #4                |

注:#5 是 **触发** dev 真实出活的关键事件;orchestrator 看到它会
走 `channel_adapter.dispatch_reply_request()` → 真 LLM。

## 成功判据 (success criteria)

按时间窗口顺序,events.jsonl 必须出现:

1. `channel.agent.reply.started` ── 60s 内,actor 来自 orchestrator,
   payload.target_member_id=`dev-cc-1`
2. `channel.message.posted` (dev reply) ── 300s 内,actor=`dev-cc-1`,
   role=`assistant`
3. `channel.agent.reply.completed` ── 紧跟 #2,reason 非空
4. 可选 (会让 L4 升级到 micro-L5):operator 紧接着 mention reviewer
   再来一轮 reply,看是否能在 300s 内拿到 reviewer 的 reply

总 wall-clock 预算:**600s**(10 min)。超时即 fail,runner 必须返回
`status=timeout`。

## 失败模式 (expected failure surface)

| 失败原因                          | 信号                                              |
| --------------------------------- | ------------------------------------------------- |
| 编排器没启动                      | wait-loop 始终拿不到 `channel.agent.reply.started`|
| backend session 没起来            | `channel.agent.reply.failed`,reason 含 `provider`|
| claude/codex 没 login             | 同上,reason 含 `auth` / `binding is missing`     |
| schema gate reject 我们的种子事件 | `event.rejected` 或 emit 子命令返回非 0           |
| 600s 超时                         | runner 主动 abort,写 timeout 行到 results.tsv    |

runner 必须把每个失败都 **明确归类**,不能"silent stall"。

## 结果记录

每次 live run 在 `tests/longhorizon/results.tsv` 追加一行,column
schema 兼容 LH-6:`iteration commit vcr mtts cost_per_task
rework_ratio guard_status note`。L4 跑只填:

- `vcr` = 1 if 成功 else 0
- `mtts` = #1 → #3 的秒数
- `cost_per_task` = `-`(无成本探针)
- `note` = `l4-channel-real: <channel_id> <pass|fail|timeout>`

## 与 L5 的关系

L4 是 L5 的**最小可观察 brick**:只要 L4 跑通,L5 (4-agent epic) 才
有意义跑。L4 失败 → 先修 channel 链,不要急着上 L5。
