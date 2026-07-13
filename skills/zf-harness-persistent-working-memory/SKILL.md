---
name: zf-harness-persistent-working-memory
description: "Use for ZaoFu worker roles to persist key findings, evidence, decisions, and failed attempts as kernel-consumable artifacts — evidence_refs on completion events, valid memory.note payloads, and the rework/dev.blocked auto-promote chain — instead of leaving them in chat history that is lost on context reset."
---

# Skill: zf-harness-persistent-working-memory

> Sprint: ZF-PWF-SKILL-001 (doc 41 §5)
> 目标角色: all worker roles (dev / review / test / judge / arch / critic)
> 状态: net-new

## 目的

让 worker 把**关键发现、证据、失败尝试**沉淀成 kernel 能真正消费的
artifact，而不是只留在聊天历史里——上下文一重置就丢。硬要求只有一条:
完成事件带 `evidence_refs`(completion-honesty 与 State Packet 都读它)。
决策与跨会话经验是**可选沉淀**,按 K5(2026-06-11)briefing 降级决议
「非平凡经验才记」,不是每轮必写。下面每一步都标注真实消费通道,别指望
没接线的机械配对。

## 操作规约

1. **完成事件 payload 必含 `evidence_refs`**(硬要求)

   每个完成事件 (`dev.build.done` / `review.approved` / `test.passed` /
   `judge.passed`) 的 payload 必须含 `evidence_refs`。这是唯一被内核硬读
   的字段:completion-honesty (`completion_honesty.py`)、contract 闭合
   (`housekeeping.py`)、State Packet evidence
   (`state_packet_projector.py` `_build_evidence`) 都消费它。

   ```json
   {
     "dispatch_id": "<from briefing>",
     "evidence_refs": [
       {"kind": "test", "path": ".zf/runs/...", "status": "passed"},
       {"kind": "git", "path": "abc123", "status": "committed"}
     ],
     "decisions": [
       "chose approach A because constraint X"
     ]
   }
   ```

   `decisions` 是**建议字段,不是机械配对**:它只被 channel owner-report
   投影 (`channel_projection.py` `_apply_owner_report`) 消费,服务于周期/
   owner 汇报事件;State Packet projector 把 `decisions` 恒置空
   (`state_packet_projector.py` `StatePacketProjector.project`),完成事件里的 decisions **不会**
   进 SP-001。要让"为什么"跨会话可读,靠第 3 步的 memory.note 或 commit,
   别指望 decisions 重建 State Packet。

2. **关键发现写到 research artifact(纪律约定)**

   遇到非常规决策点(外部 API 行为、文档/代码 mismatch 等),写
   `docs/research/<task_id>-<topic>.md`——这是**纪律约定,不是 projection
   直读**:运行时投影调用点 (`orchestrator_dispatch.py`) 传入的
   `research_paths` 恒为空,findings.md 不会自动扫 `docs/research`。要让
   它被消费,把该 artifact 路径塞进完成事件的 `evidence_refs`
   (evidence_refs 被广泛消费,见第 1 步)。**不要只在聊天里说**。

3. **失败痕迹走 rework / `dev.blocked` 事件链**

   失败不靠逐次手写 memory.note 留痕。kernel 会把 `dev.blocked` 与
   `candidate.conflict` **自动 promote** 成 memory.note 落进 MemoryStore
   (`housekeeping.py` `promote_to_memory_note_event`),下一持有者的
   briefing memory 注入会带回教训。retry 前读失败历史、3 次同型换策略/
   升级的完整规约见 `zf-harness-error-attempt-ledger`(第 3 步委托它)。

   若确有**非平凡**经验值得主动沉淀,emit 一条**合法** memory.note——
   payload 必须含 `mem_type`(∈ decision/pattern/fix/context)与 `content`,
   否则 `apply_memory_note_event` 会静默丢弃(缺字段 = 白写):

   ```bash
   zf emit memory.note --actor <instance_id> --task <task_id> \
     --payload '{"mem_type":"fix","content":"<task_id> 失败教训:<短句>"}'
   ```

   这与 briefing 里 `injection.py` 现行的单行可选提示一致(K5 2026-06-11
   降级:例行工作跳过 memory.note、kernel 已自动 promote 关键事件)——
   高频逐次 emit 是**已被审计否决**的高噪声规则。注意:
   `.zf/projections/tasks/<task_id>/attempt-ledger.md` 只渲染 **rework
   事件**,不读 memory.note——想留 attempt 痕迹靠 rework/blocked 事件链,
   不是往 memory.note 塞 category 字段。

4. **不要把决策只写在 commit message 里**

   commit message 是 audit 用的;决策的"为什么"若要跨会话被下一个
   worker/操作员读到,靠合法 memory.note(第 3 步)沉淀——`git log`
   之外也能读懂。

## 反模式

- ❌ "我已经修好了" 不带 evidence_refs
- ❌ emit memory.note 但 payload 缺 mem_type/content(被静默丢弃,白写)
- ❌ 把失败痕迹往 memory.note 塞,却指望 attempt-ledger.md 显示(它只读 rework 事件)
- ❌ 重要经验只出现在聊天 transcript,重启后丢失

## 守护测试

`tests/test_pwf_invariants.py::test_inv_i61_projection_header_invariant`
锁定 4 个投影文件的 header 不变量(projection only / source_events /
state_packet_ref / generated_at)。内容侧是可观察信号而非内核门:worker
不写 evidence_refs 时 findings.md 落 "_No findings recorded yet._",
review 时立刻可见。

## 关联

- `skills/zf-harness-error-attempt-ledger/SKILL.md` — 失败/3-Strike/升级链完整规约(第 3 步委托它)
- `yoke/context-hygiene/SKILL.md` — 上下文卫生方法论
- `src/zf/runtime/state_packet_projector.py` (`_build_evidence`) — 从完成事件类型 + evidence_refs.path 建 State Packet evidence(decisions 恒空)
- `src/zf/runtime/housekeeping.py` (`promote_to_memory_note_event` / `apply_memory_note_event`) — dev.blocked/candidate.conflict 自动 promote + 合法 memory.note 落库
- ZF-PWF-MEM-001 (4-file projection;research_paths 运行时恒空,见第 2 步)
