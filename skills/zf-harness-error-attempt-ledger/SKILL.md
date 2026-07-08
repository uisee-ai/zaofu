---
name: zf-harness-error-attempt-ledger
description: "Use for ZaoFu dev / test / review workers to log every failed attempt, break out of same-signature failure loops after 3 strikes, and escalate via role failure events into the kernel human.escalate → diagnosis.requested chain."
---

# Skill: zf-harness-error-attempt-ledger

> Absorbs zf-harness-break-loop.
> Sprint: ZF-PWF-SKILL-004 (doc 41 §5) + ZF-LH-BREAK-LOOP-001 (doc 26 §5.3)
> 目标角色: dev / test / review (existing critic 3-Strike augment)
> 状态: 合并后的单一「attempt-ledger 纪律」skill

## 目的

**3 次同型失败必须换策略或升级**。当前只有 critic skill 内置 3-Strike
规则;本 skill 把同样的纪律扩展到 dev / test / review,覆盖完整闭环:
写入端(每次失败留痕)+ 读取端(retry 前先读失败历史)+ 破循环
(3 次同型 → 换 approach 或升级)。

「同型」判定与 kernel `TaskAttemptLedger`
(`src/zf/runtime/attempt_ledger.py`)对齐:计数键是
**(task, stage, failure 签名)**,supersede/重放的终态**不算一轮**;
environment 类不可重试签名由 kernel 直达 deadletter,worker 不要
对这类失败反复重试。

## 操作规约

### 1. 每次失败 emit `memory.note category=failed_attempt`

```bash
zf emit memory.note --actor <instance_id> --task <task_id> \
  --payload '{
    "category": "failed_attempt",
    "attempt_n": <1|2|3>,
    "approach": "<short tag, e.g. async-rewrite>",
    "failure_reason": "<short>",
    "evidence_ref": "<.zf/runs/.../test.log>"
  }'
```

`memory.note` 经 housekeeping 落进 MemoryStore,后续 briefing 的
memory 注入会把教训带回给下一个持有者。payload 里的
category/attempt_n/approach 等字段是 **skill-owned 约定(无内核
校验)**,价值在于人和后续 agent 可读。

### 2. retry 前 — 先读失败历史

rework dispatch 后、动手 retry 前,先看本 task 的失败事件窗口:

```bash
zf events --last 50          # 找本 task 的 dev.failed / dev.blocked / rework 事件
grep '<task_id>' .zf/events.jsonl | tail -20
```

注意:`.zf/projections/tasks/<task_id>/attempt-ledger.md` 投影的
运行时调用点(`orchestrator_dispatch.py`)目前传入的 rework_events
为空,该文件恒为 "No rework attempts yet"——**不要依赖它**,以
事件窗口 + kanban 为准。

### 3. 第 2 次同型失败 — STOP,换策略

如果 attempt 2 与 attempt 1 是**同一 failure 签名**(同样思路的
变体、同类 failure_reason):

- 不要直接 retry attempt 3
- 重读失败窗口里 attempt 1/2 留下的失败原因
- 换 approach(数据结构换、依赖换、抽象层级换),并 emit
  `memory.note` 解释为什么换:

```bash
zf emit memory.note --task <task_id> --actor <instance_id> \
  --payload '{
    "category": "break_loop",
    "loop_pattern": "<同型失败的描述>",
    "new_approach": "<新方向描述>",
    "why_new_approach_should_work": "<...>"
  }'
```

### 4. 第 3 次同型失败 — 升级

同 task 已 3 次同一签名失败(supersede/重放不计),或想不出新
approach:**停止重试**,经角色 failure 事件带上 3-Strike 理由:

```bash
zf emit dev.blocked --actor <instance_id> --task <task_id> \
  --payload '{
    "reason": "3-strike: <task_id> failed 3 same-signature attempts",
    "attempts": ["<approach tag 1>", "<tag 2>", "<tag 3>"]
  }'
```

(test / review 角色同理用自己的 failed/blocked 事件;`attempts`
字段是 skill-owned 约定,无内核校验。)

升级由 kernel 接管,worker 只负责把证据送进链路:失败/停滞信号 →
`human.escalate`(`escalation.py`,同签名节流)→ 指纹判重后铸
`diagnosis.requested` 交 Tier-2 diagnostician
(`src/zf/runtime/diagnosis.py`)→ `diagnosis.completed`。
worker **不要**直发 `diagnosis.requested`(仅内核铸造)。

不要用 `task.done.blocked` 表达 3-Strike 升级:该事件被 rework
triage 判为 evidence gap,路由 judge 补证据
(`rework_triage.py`),不进人类升级链;它的正确用途是"完成受阻于
证据缺口"。payload 里的 `escalate_to` 字段无内核消费者,不要写。

kernel 侧的硬上限是 I36 rework-cap(守护事件 `task.rework.capped`,
KERNEL_INVARIANTS.md §I36),按 TaskAttemptLedger 的
(task, stage, failure 签名) 计数封顶。本 skill 负责让 worker 在到
cap 之前就主动放弃同型尝试。

## 反模式

- ❌ 失败了但不 emit memory.note(失败教训不进 MemoryStore)
- ❌ 失败 3 次都用同一个 approach(应该换策略)
- ❌ 不看失败事件窗口就开始 retry
- ❌ 死撑到 rework-cap(`task.rework.capped`),浪费 turn
- ❌ 用 task.done.blocked + escalate_to 求升级(会被路由去补证据)

## 守护测试

`tests/test_working_memory_projection.py::test_attempt_ledger_lists_rework_events`
锁定 attempt-ledger.md 的渲染形态(单元级直喂 rework_events;运行时
调用点喂空,见操作规约 2 的注意)。

## 关联

- `skills/zf-yoke-critic-role-context/SKILL.md` (原 3-Strike 实现)
- `src/zf/runtime/attempt_ledger.py` (TaskAttemptLedger:同型签名与计数语义)
- `src/zf/runtime/diagnosis.py` (Tier-2 `diagnosis.requested` 铸造)
- ZF-PWF-MEM-001 (attempt-ledger.md projection;运行时断链见操作规约 2)
- I36 rework-cap(守护事件 `task.rework.capped`)
