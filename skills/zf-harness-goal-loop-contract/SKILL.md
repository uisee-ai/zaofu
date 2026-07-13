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
  feature.status == "done"
  AND all task.status ∈ {done, archived}
  AND release/ship evidence present
  AND latest State Packet.next_event == ""
  AND no unrecognized git working-tree changes
```

具体落地（命令均在 src/zf 有实现）：

```bash
# Feature 处于终态 done（feature 合法状态 planning|active|done|cancelled，
# 无 "delivered"；见 core/feature/schema.py:_VALID_STATUSES）
zf feature list --status done | grep -q "$FEATURE_ID"

# 该 feature 下已无非终态 task（export --feature 按 contract.feature_id 过滤）
zf kanban export --feature "$FEATURE_ID" --format json \
  | jq -e 'length > 0 and all(.[]; .status == "done" or .status == "archived")' >/dev/null
# 或全局非终态清单：zf kanban open

# State Packet next_event 为空（task ready to ship）
jq -r '.next_event' .zf/state/state-packet.json | grep -qE '^(\s*|null)$'

# 工作树干净
test -z "$(git status --porcelain)"
```

达成后把 feature 落终态要走 FeatureStore（`zf feature update <id> --status done`）——
手动 `zf emit feature.status_changed status=delivered` 不写 FeatureStore
（store.py `FeatureStore.add`/`FeatureStore.update` 只认 `_VALID_STATUSES`，且无 delivered）。

### Loop 调度

`autopilot.schedules` 的真实 schema 只有 `id` / `interval` / `action`
（schema.py:`AutopilotScheduleConfig`），`action` 白名单当前仅 `triage`
（loader.py:`_VALID_AUTOPILOT_ACTIONS`）。autopilot tick 只写
`autopilot.proposal.created`（proposal_only），没有内核级 goal-poll。
唯一可加载形状：

```yaml
# zf.yaml —— 多余键（name / cron / action:emit / event_type）会直接 ConfigError
autopilot:
  enabled: true
  mode: proposal_only
  schedules:
    - id: long-horizon-triage
      interval: 10m
      action: triage
```

> **未实施设计（P2）**：把 goal condition 挂到定时 `orchestrator.goal.poll`
> 事件上自动判定，是 doc 41 §5 的 P2 follow-up。src/zf 中既无
> `orchestrator.goal.poll` 也无 goal-poll action，**不要写成现行配置**。
> 现状下 goal condition 由 orchestrator/operator 在 triage / 收口时运行上面
> 的 bash 检查（可由 triage tick 触发），判定权仍在 deterministic gate，
> 不在 LLM 自评。

### 现行完成判定机械（重试非无限轮询）

goal loop 的"不满足就再判一次"不是无约束轮询——内核已有判定/抑制/升级
合约，技能应**引用**这些信号，被抑制或已升级就停轮询、走 escalate：

- **completion audit**：终态收口由 completion audit 通道裁决
  （事件 `completion_audit.started` / `completion_audit.routed`）。
- **provider stop 守卫**：`provider.stop.check`（hook_registry / hook_recv）
  在缺 verification_tiers 等前置未满足时挡下 provider 侧"我完成了"的 Stop。
- **判审收敛门（FIX-15，orchestrator_fanout.py:`_delta_gate_allows`）**：
  同一 `target_commit` 已败审时重触发被 `fanout.retrigger.suppressed` 抑制
  （无 delta 不重开审）；同段连续 ≥3 次驳回未收敛 → 写 `human.escalate`
  reason=`judge_nonconvergence`（带驳回链摘要），不再自动重试。
- **plan 重铸判重**：同 stage+pdd+task 集的未决 plan 会被
  `plan.minting.suppressed`（orchestrator_fanout.py）判重，不铸新单。

### Goal-mode 生命周期事件（kernel 真实事件）

`goal.enabled`（schema.py `GoalConfig.enabled`，灰度默认关）开启后，goal 回路的生命周期由这组
**kernel 事件**驱动（均登记于 known_types.py `KNOWN_EVENT_TYPES`）。投影
`build_run_goal_projection` 消费它们给出 `status`（run_manager.py）。
技能判定 goal 状态时读这些事件 / `zf goal show`，**不要靠 LLM 叙述**：

| 事件 | 发射点（anchor） | 语义 / 投影 status |
|---|---|---|
| `run.goal.started` | `zf submit` accepted 后 kernel 铸造（cli/flow.py `apply_flow_submit`，gated by `goal.enabled`） | goal 开跑，`run_id`=correlation_id；投影 → `active`（run_manager.py `build_run_goal_projection`） |
| `run.goal.updated` | `zf goal set`（actor=operator，cli/goal.py `_run_set`）；cost/usage/provider-stop 限流时 kernel 补发（orchestrator_reactor.py `_on_cost_budget_exceeded`/`_emit_goal_limited_status`，status=`budget_limited`/`usage_limited` 等） | 改 objective/status，投影按 payload.status 更新（run_manager.py `build_run_goal_projection`）；**也是唤醒事件**（见下） |
| `run.goal.completed` | kernel 在真终态成功（`judge.passed`）落地且仍有活 goal 时铸造（`run_goal_completion_event`，run_manager.py） | 投影 → `complete`（run_manager.py `build_run_goal_projection`），列入终态信号 `_TERMINAL_SIGNAL_EVENTS`（run_manager.py） |
| `run.goal.blocked` | goal 阻塞态；投影 → `blocked`（run_manager.py `build_run_goal_projection`），登记为问题（event_problem_registry.py `EVENT_PROBLEM_SPECS`）/失败信号（failure_signals.py `_RUN_COMPLETED_REOPEN_EVENTS`）。`zf goal set --status blocked` 走 `run.goal.updated` 同样落 `blocked` | 走 escalate/人工，不再自动重试 |
| `run.goal.quiescent.entered` | `mark_quiescent_transition`（quiescent.py），tick 服务每 tick 判（tick_services.py `run_standard_tick_services`） | 终局 escalate 后进入静默：tick 服务全体跳过（`return TickServiceResult()`），不再空烧 |
| `run.goal.quiescent.exited` | 同上（quiescent.py `mark_quiescent_transition`） | 唤醒后退出静默，tick 恢复点火 |

**Provider-native goal 完成 → kernel 事件的映射**：provider 端 `/goal complete`
（Claude / Codex）**不自己发** `run.goal.completed`。该事件只由 kernel 在
**真终态成功事件（`judge.passed`）落地且存在真实 `run.goal.started`
（run_id 非空）**时确定性铸造（run_manager.py `run_goal_completion_event`）。这正是"provider goal
只作辅助、deterministic gate 定终局"在事件层的落法——provider 自评永远进不了
`complete` 投影，除非 gate 侧真过审。

**Quiescent（静默）进入 / 退出语义**（灰度：`goal.enabled` 且
`goal.quiescent_after_escalate`，默认后者 True、前者 False = 零回归；
quiescent.py `_enabled`）：

- **进入**：最近一次 `human.escalate` 之后既无进展事件（`_PROGRESS_SUCCESS_EVENTS`）
  也无唤醒事件，且已过宽限窗 `_GRACE_SECONDS`（600s，给 RM 自愈周期留路）→
  `quiescent_now` 判 quiescent，tick 服务全体跳过（escalate = 干净等人，不是每
  5s 空烧的 r6.1 4h/6.4M 实弹教训）。
- **退出 / 唤醒事件**（quiescent.py `_WAKE_EVENT_TYPES`）：`user.message` / `user.intent.submitted` /
  `runtime.resume.requested` / `runtime.attention.acknowledged` / `run.goal.updated` /
  `dispatch.resumed` / `loop.resume_requested`——kernel 自噪音（tick 自身产物）**不算**
  唤醒，否则静默永不生效。所以 `zf goal set --status active` 会经 `run.goal.updated`
  唤醒回路（codex re-activate 语义）。

### 小模型 / provider goal 只作辅助

provider 端的 goal (Claude `/goal`、Codex 同等) 可以作为**循环结
束的辅助信号**，但**最终判定**仍走 zaofu deterministic state/gates（映射见上
「Goal-mode 生命周期事件」——只有 kernel 铸造 `run.goal.completed`）。

### Lane goal continuation

fanout / affinity lane 场景下,provider goal 可以绑定到单个 lane:

```text
feature goal -> wave goal -> lane goal
```

约束:

- lane goal 只能证明当前 `fanout_id/child_id/task_id` 的 assigned slice。
- provider `goal complete` 不等于 ZaoFu 的 task done、`candidate.ready` 或
  feature 终态 done。
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
