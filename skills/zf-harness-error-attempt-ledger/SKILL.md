# Skill: zf-harness-error-attempt-ledger

> Sprint: ZF-PWF-SKILL-004 (doc 41 §5)
> 目标角色: dev / test / review (existing critic 3-Strike augment)
> 状态: augment (扩展现有 `zf-yoke-critic-role-context` 的 3-Strike)

## 目的

**3 次同型失败必须换策略或升级**。当前只有 critic skill 内置 3-Strike
规则；本 skill 把同样的纪律扩展到 dev / test / review。

attempt-ledger.md projection（MEM-001）读取 rework events，本 skill
要求 worker emit 足够细节让 ledger 有用。

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

### 2. 第 2 次同型失败 — STOP，换策略

如果 attempt 2 与 attempt 1 是**同样思路的变体**（同样的 approach
tag）：

- 不要直接 retry attempt 3
- 重读 `attempt-ledger.md` 看 attempt 1/2 留下的失败原因
- 在 completion event payload 写 `notes: "strategy_change: <new approach>"`

### 3. 第 3 次失败 — 升级

如果同 task 已经 3 次连续同型失败（无论是否换策略），**强制升级**：

```bash
zf emit task.done.blocked --actor <instance_id> --task <task_id> \
  --payload '{
    "reason": "3-Strike: <task_id> failed 3 attempts",
    "attempts": [<list of approach tags>],
    "escalate_to": "human"
  }'
```

kernel 的 `max_rework_attempts` 已经在 retry-cap 层守护，本 skill
负责让 worker 在到 cap 之前就主动放弃同型尝试。

## 反模式

- ❌ 失败了但不 emit memory.note（attempt-ledger projection 空）
- ❌ 失败 3 次都用同一个 approach（应该换策略）
- ❌ 看不到 attempt-ledger 就开始 retry

## 守护测试

ZF-PWF-MEM-001 projection 渲染 attempt-ledger.md 必须显示
rework events。`tests/test_working_memory_projection.py::
test_attempt_ledger_lists_rework_events` 锁定输出形态。

## 关联

- `skills/zf-yoke-critic-role-context/SKILL.md` (原 3-Strike 实现)
- ZF-PWF-MEM-001 (attempt-ledger.md projection)
- kernel `max_rework_attempts` retry-cap (I36)
