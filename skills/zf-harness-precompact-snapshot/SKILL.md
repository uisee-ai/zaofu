# Skill: zf-harness-precompact-snapshot

> Sprint: ZF-PWF-SKILL-002 (doc 41 §5)
> 目标角色: dev / review / test / judge
> 状态: net-new

## 目的

context 即将 compact / recycle 之前，worker MUST 产生一个**结构化
snapshot**，让 zaofu kernel 能投影到 State Packet。聊天历史可丢，
但 snapshot 必须够 next worker 接力。

## 操作规约

当 worker 收到 `worker.context.precompact` 信号（Claude Code
PreCompact hook 已接入，commit `2005a6e`），或自己判断
context > 80% 时，**立刻**：

1. **emit `memory.note` 记录当前状态**

   ```bash
   zf emit memory.note --actor <instance_id> --task <task_id> \
     --payload '{
       "category": "precompact_snapshot",
       "current_step": "<我当前在做的步骤>",
       "remaining_work": ["<下一步 1>", "<下一步 2>"],
       "blockers": [],
       "decisions_made_this_turn": ["<决策 1>", "<决策 2>"],
       "files_modified": ["src/a.py", "src/b.py"]
     }'
   ```

2. **git commit & push 当前工作**

   ```bash
   git add -A
   git commit -m "wip: <task_id> snapshot before compact"
   ```

3. **不阻塞 compact** — exit 0 / 让 hook 继续

   PreCompact hook 是 informational signal，不能拒绝 compaction。
   `worker.context.snapshot_requested` 事件由 kernel 自动 emit
   触发 State Packet 重建。

## 反模式

- ❌ 接到 precompact 信号继续闷头干（不 emit memory.note）
- ❌ 把 snapshot 写成长篇散文 — 用结构化 payload，便于 projector 解析
- ❌ 试图阻止 compact 完成（hook 应 exit 0）

## 守护测试

`tests/test_precompact_snapshot_requested.py` —
PreCompact → snapshot_requested 事件链。

## 关联

- ZF-PWF-PRECOMPACT-001 (Claude PreCompact hook integration)
- ZF-LH-SP-001 (State Packet 重建)
- ZF-PWF-MEM-001 (4-file projection rebuild on snapshot_requested)
