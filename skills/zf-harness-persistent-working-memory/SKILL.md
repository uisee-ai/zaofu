# Skill: zf-harness-persistent-working-memory

> Sprint: ZF-PWF-SKILL-001 (doc 41 §5)
> 目标角色: all worker roles (dev / review / test / judge / arch / critic)
> 状态: net-new

## 目的

要求 worker 在每一轮输出里把**关键发现、错误、测试结果、决策**写进结构化
artifact / completion payload，而不是只留在聊天历史里。zaofu kernel
会用这些 artifact 重建 State Packet（SP-001）和 4-file projection
（MEM-001）；如果 worker 不显式声明，下一次恢复时上下文丢失。

## 操作规约

当本 skill 被加载时，worker MUST：

1. **完成事件 payload 必含 `evidence_refs`**

   每个完成事件 (`dev.build.done` / `review.approved` / `test.passed` /
   `judge.passed`) 的 payload 必须含：

   ```json
   {
     "dispatch_id": "<from briefing>",
     "evidence_refs": [
       {"kind": "test", "path": ".zf/runs/...", "status": "passed"},
       {"kind": "git", "path": "abc123", "status": "committed"}
     ],
     "decisions": [
       "chose approach A because constraint X",
       "rejected approach B due to concurrency issue"
     ]
   }
   ```

2. **关键发现写到 research artifact**

   遇到非常规决策点（外部 API 行为、文档/代码 mismatch 等），写
   `docs/research/<task_id>-<topic>.md` 并在完成事件 `evidence_refs`
   里引用它。**不要只在聊天里说**。

3. **错误 / 失败留 attempt 痕迹**

   每次失败尝试 emit 一条 `memory.note` event：

   ```bash
   zf emit memory.note --actor <instance_id> --task <task_id> \
     --payload '{"category": "failed_attempt", "approach": "<short>",
                "failure_reason": "<short>"}'
   ```

   下一次 rework 时 attempt-ledger projection 会读到这条。

4. **不要把决策只写在 commit message 里**

   commit message 是 audit 用的；决策本身的"为什么"应该在
   evidence_refs.decisions 或 memory.note 中，便于后续 worker / 操作员
   不通过 `git log` 也能读懂。

## 反模式

- ❌ "我已经修好了" 不带 evidence_refs
- ❌ 多次失败但 attempt-ledger 全空（说明 worker 没 emit memory.note）
- ❌ 重要决策只出现在聊天 transcript，重启后丢失

## 守护测试

`tests/test_pwf_invariants.py::test_inv_i61_*` — 投影文件必须可
从干净的 state_dir + events.jsonl 重建出有意义的 plan/findings/
progress/attempt-ledger 内容。如果 worker 不按本 skill 写
artifact，4 文件会全是 "_No findings recorded yet._" — review 时立刻可见。

## 关联

- ZF-LH-SP-001 (State Packet) — 消费本 skill 产出
- ZF-PWF-MEM-001 (4-file projection) — 渲染本 skill 产出
- ZF-PWF-ATTEST-001 (artifact attestation) — frozen 后可校验
