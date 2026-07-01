---
name: zf-harness-goal-loop-contract
description: "Use for ZaoFu long-horizon goal loops where provider-native goal completion must remain advisory and deterministic runtime gates decide final completion."
---

# Skill: zf-harness-goal-loop-contract

> Sprint: ZF-PWF-SKILL-005 (doc 41 §5)
> 目标角色: orchestrator / operator
> 状态: net-new

## 目的

long-horizon `goal` / `loop` 的终止条件必须**绑定 zaofu deterministic
gates**，不是 LLM 自评 "差不多了"。聊天可以自评，运行时必须以
events.jsonl + State Packet + gate evidence 为准。

## 操作规约

### Goal condition 必含的 deterministic 检查

```text
goal condition (Python expression / bash check):
  feature.status == "delivered"
  AND all task.status ∈ {done, archived}
  AND release/ship evidence present
  AND latest State Packet.next_event == ""
  AND no unrecognized git working-tree changes
```

具体落地：

```bash
# Feature 全部 delivered
zf feature list --status delivered | grep -q "$FEATURE_ID"

# 所有 task 处于终态
test -z "$(zf kanban show --feature "$FEATURE_ID" --status in_progress)"

# State Packet next_event 为空（task ready to ship）
jq -r '.next_event' .zf/state/state-packet.json | grep -qE '^(\s*|null)$'

# 工作树干净
test -z "$(git status --porcelain)"
```

### Loop 调度

zaofu autopilot.schedules 提供周期触发；goal/loop skill 把"何时
认为目标已达"的判定交给 deterministic check，让 LLM 不能宣称完成。

```yaml
# zf.yaml
autopilot:
  schedules:
    - name: long-horizon-goal-poll
      cron: "*/10 * * * *"
      action: emit
      event_type: orchestrator.goal.poll
```

orchestrator 收到 `orchestrator.goal.poll` 后，自动跑 goal condition
脚本；满足 → emit `feature.status_changed status=delivered`；
不满足 → 写 progress note，下个 tick 再判。

### 小模型 / provider goal 只作辅助

provider 端的 goal (Claude `/goal`、Codex 同等) 可以作为**循环结
束的辅助信号**，但**最终判定**仍走 zaofu deterministic state/gates。

### Lane goal continuation

fanout / affinity lane 场景下,provider goal 可以绑定到单个 lane:

```text
feature goal -> wave goal -> lane goal
```

约束:

- lane goal 只能证明当前 `fanout_id/child_id/task_id` 的 assigned slice。
- provider `goal complete` 不等于 ZaoFu `task.done`、`candidate.ready` 或
  `feature.delivered`。
- lane worker 完成声明必须带 `task_map_ref`、`lane_id` / `stage_slot`
  (若有)、`source_commit`、`files_touched`、`evidence_refs`。
- 缺证据时走 continuation;真 blocker 走 blocked/suspend;最终接收仍由
  writer fanout admission、candidate integration 和 gate 决定。

具体 lane prompt 和 payload 形状见 `zf-harness-lane-goal-continuation`。

## 反模式

- ❌ goal condition 写 "feature 应该差不多了" — 必须是机器可判
- ❌ provider goal 满足就直接 stop orchestrator
- ❌ Loop 里没有 deterministic check，靠 LLM 看 "事情看起来 OK"
- ❌ lane worker 把自己的 slice 完成声明成 feature / product 完成

## 守护测试

`tests/test_pwf_invariants.py::test_inv_i64_*` 守护
`provider.stop.check` 不在缺 verification_tiers 时误阻。
Goal-loop 守护测试是 P2 follow-up（autopilot.schedules
集成 + goal poll event）。

## 关联

- ZF-LH-SP-001 (State Packet.next_event 是 goal check 主信号)
- ZF-PWF-STOP-GUARD-001 (provider stop pre-check)
- `prompt/long-horizon-pwf-goal-loop.md` (operator-facing goal 模板)
