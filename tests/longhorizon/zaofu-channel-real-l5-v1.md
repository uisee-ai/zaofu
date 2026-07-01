# L5 真实化场景 — 4-Agent Long-Horizon Epic (zaofu-channel-real-l5-v1)

状态:**deferred actual run**。本文档是 L5 跑通前的设计稿,只在 L4
连续 3 次 pass 之后才考虑实跑(避免在烧 $20+ LLM 预算前先把 channel
链本身的 bug 暴露干净)。

## 目标

在 cj-mono 的 mixed backend 之上,跑一个 4-agent epic 跨多个 task,
带 arch / dev / review / test 的全角色协作,看 zaofu 的:

- channel 多 thread 切换
- exclusive_files / shared_files fanout 独立性检查 (recon brief 提到的
  `_check_fanout_independence`)
- workflow.invoke.requested → workflow.invoke.accepted → task.fanout.requested
  完整 reactor 链
- 长 horizon (> 2h) 下的 wake coalescing 行为(参考 memory:
  wake-storm 当前 interim fix vs doc 64 idle-gated 设计)

## Channel / Thread 拓扑

```
   ch-l5-epic-{ts}
       │
       ├── thr-design           (arch + critic)
       ├── thr-impl-A           (dev-cc-1 + reviewer-cdx-1)
       ├── thr-impl-B           (dev-cdx-2 + reviewer-cc-2)
       └── thr-judge            (test + judge)
```

跨 thread 的 hand-off 走 `channel.message.posted` with `mentions` +
`refs.task_id`,reactor 应在 thr-design 收到 `arch.proposal.done` 后
**自动** propose fanout 到 thr-impl-A / thr-impl-B。

## Members (8 个,真 LLM)

| member_id    | persona | backend     | exclusive scope          |
| ------------ | ------- | ----------- | ------------------------ |
| arch-cdx     | arch    | codex       | docs/design/**           |
| critic-cc    | critic  | claude-code | docs/design/**           |
| dev-cc-1     | dev     | claude-code | src/lh_demo/module_a/**  |
| dev-cdx-2    | dev     | codex       | src/lh_demo/module_b/**  |
| review-cdx-1 | review  | codex       | (跨 module_a / module_b) |
| review-cc-2  | review  | claude-code | (同上)                   |
| test-cc      | test    | claude-code | tests/lh_demo/**         |
| judge-cdx    | judge   | codex       | (read-only across repo)  |

exclusive_files 故意拆成 module_a / module_b,验
`_check_fanout_independence` 不误判。

## Epic 内容

一个 fake "构建 demo 包 (lh_demo)" 任务:

1. arch 设计两个独立模块 module_a (parser) + module_b (formatter)
2. dev-cc-1 实现 module_a,dev-cdx-2 实现 module_b — 并行
3. reviewer 双向 review,test 写交叉 test,judge 出 verdict
4. 全程不准退出 channel(测 long-horizon membership 持久化)

## 预算

| 维度       | 上限           |
| ---------- | -------------- |
| wall-clock | 4 h            |
| LLM USD    | $25            |
| events     | ≤ 800 / task   |
| tasks      | ≤ 6            |

超 wall-clock 或 USD 任何一个 → runner 主动 `zf stop` 并写 timeout
行。

## 成功判据 (高阶,不展开到 event level)

- 每个 task 都 reach `judge.passed` 或 `judge.failed`(不卡在 pending)
- channel 里看到 ≥ 2 次 cross-member mention(测真互动,不是各干各的)
- fanout 至少 1 次 propose + accept,且 `exclusive_files` 检查未误判
- 没有 silent stall:任何 ≥ 5min 无事件的窗口,要么有
  `dispatch.silent_stall` 事件,要么 `worker.progress`

## 跑前必须先 pass 的检查

- [ ] L4 (`zaofu-channel-real-l4-v1`) 连续 3 次 pass
- [ ] cj-mono `.zf-mixed/` 存在且 events.jsonl 干净 (< 100 行 stale 残留)
- [ ] `global_budget_usd` 至少 100,且 `budget_enforcement_enabled: true`
  (跟当前 `false` 不一样 — 跑 L5 前要手动改)
- [ ] tmux session `zf-mixed` 跑稳 ≥ 30 min,无重启
- [ ] codex + claude-code login 双双有效

## runner 状态

不实现 live 模式。`run_zaofu_channel_real.py --scenario l5` 当前只
会打印 "deferred — see zaofu-channel-real-l5-v1.md" 并 exit 2。

正式跑通后,把 deferred 限制摘掉,scenario 步骤从本文档下放到 runner。
