# Skill: zf-harness-break-loop

> Sprint: ZF-LH-BREAK-LOOP-001 (doc 26 §5.3)
> 目标角色: dev / test / review / judge
> 状态: net-new (P2)

## 目的

**死循环 rework 自救**。当 worker 发现自己在同型失败循环里
(同 task 3+ 次失败 + 同类 evidence)，主动 break out，不要等
`max_rework_attempts` 上限触发 escalation。

核心约束：让 worker meta-认知
失败模式。

## 操作规约

每次 rework dispatch 时，worker 先**读 attempt-ledger.md**：

```text
.zf/projections/tasks/<task_id>/attempt-ledger.md
```

如果发现 **≥ 3 条** rework events 都属于**同一 approach tag** 或
**同一 failure_reason 类别**：

1. **不要再用同样思路**。换 approach（数据结构换、依赖换、抽象
   层级换）。
2. emit `memory.note` 解释为什么换：

   ```bash
   zf emit memory.note --task <task_id> --actor <instance> \
     --payload '{
       "category": "break_loop",
       "loop_pattern": "<3 次同型失败的描述>",
       "new_approach": "<新方向描述>",
       "why_new_approach_should_work": "<...>"
     }'
   ```

3. 如果想不出新 approach，**主动升级**：

   ```bash
   zf emit task.done.blocked --task <task_id> --actor <instance> \
     --payload '{"reason": "stuck in 3-loop, need human strategy"}'
   ```

## 反模式

- ❌ 失败 3 次还用一模一样的方法
- ❌ "再试一次" 不读 attempt-ledger
- ❌ 死撑到 max_rework_attempts，浪费 turn

## 关联

- ZF-PWF-MEM-001 attempt-ledger.md projection
- `skills/zf-harness-error-attempt-ledger/SKILL.md` (写入端)
- kernel `max_rework_attempts` (I36 retry-cap)
