# Skill: zf-harness-handoff-resume-snapshot

> Sprint: ZF-SKILL-MP-007 (doc 39 §4.7)
> 目标角色: all roles
> 状态: net-new

## 目的

每个 role 完成自己的 task 时，输出**可恢复 handoff**: 后续任何 role
（人类或 LLM）只需读 handoff 就能接力，不需要回看聊天 / 翻 events.jsonl。
本 skill 把 handoff 形状 lock 到 State Packet schema。

## 操作规约

任何 role 在 `<role>.{done,passed,approved}` 事件 payload 里**必须**带：

```json
{
  "handoff_summary": {
    "what_done": "<本轮完成的具体动作>",
    "evidence_refs": [{"kind": "...", "path": "...", "status": "..."}],
    "decisions": ["..."],
    "open_questions": [],
    "next_owner_hint": "review|test|judge|...",
    "context_warnings": []
  }
}
```

这些字段映射到 `StatePacket.{completed, evidence, decisions,
blocked_by, next_owner}` —— SP-001 projector 会读取。

## 反模式

- ❌ "完成了，下一位接力" 不带 evidence_refs
- ❌ 把 decisions 写在 commit message 里而不是 payload
- ❌ open_questions 列了一堆但 next_owner_hint 还是 "继续往下"

## 关联

- ZF-LH-SP-001 (State Packet schema)
- ZF-PWF-CATCHUP-001 (transcript catchup 不能代替 handoff_summary)
