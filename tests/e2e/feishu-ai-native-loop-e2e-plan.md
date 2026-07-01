# 测试计划 — 围绕 Feishu 的 AI-native 闭环 端到端实测

> 目标:验证"operator 离场,从飞书驱动一整条 agentic 交付闭环"的**命门**——
> 真实 Orchestrator 的 plan-approval gate **真的持有** writer fanout,飞书把待审 plan **真的浮出**,
> 经**真 ControlledAction approve 路径**(飞书/Web 同款)产生的 `plan.approved` **真的解锁真实 writer fanout**。
> 锚定 docs/design `93-plan-approval-gate-kanban-agent-review.md` §9 验收 + `12-feishu-bridge` / `31-manual-transition-policy` 的控制面边界。
> 实现:`tests/test_feishu_ai_native_loop_e2e.py`,复用 `test_writer_fanout_runtime.py` 的真 Orchestrator 基座
> (`_approval_orch`/`_approval_start`)。
> 与上一份 plan(`feishu-channel-kanban-e2e-plan.md`)的区别:那份测飞书**卡面/回调插件**(合成事件);
> **这份测真 orchestrator gate↔fanout 的解锁机制**——上一轮漏掉的核心。

## 设计闭环(doc 93 §7.3 离场闭环,逐跳,已核实 src WIRED)

```
synth(operator 离场)→ task_map.ready → admission(G3 机械)
  → plan.approval.requested(gate,enabled 时)→ writer fanout HOLD(不孵化)
  → 飞书推卡(deep-link + digest 摘要)→ operator 在飞书/Web approve
  → plan.approved(actor=operator,经 ControlledAction)
  → _resume_writer_fanout_on_plan_approved 重入 → writer fanout 孵化 → agents 干活
  → 回执更新卡片 → (reject 则 → synth rework)
```
关键 src:`orchestrator_fanout.py` `_evaluate_plan_approval_gate`(1141)/ `_resume_writer_fanout_on_plan_approved`(1168);
`control_actions_plan.py:46`(approve/reject emit);`integrations/feishu/plan_approval_card.py`(卡面)。

## 控制面边界(必须不被违反)

- 飞书**不持有 truth**、不写 kanban.json/events.jsonl;只 notify + 经 ControlledAction 请求 mutation(doc12)。
- `done` 只能走 terminal gate;**agent 可建议绝不代点**,`plan.approved` 的 actor 必须是 operator(doc93 §7.1)。
- gate disabled(缺省)→ kernel **auto-mint** `plan.approved{auto:true}`,行为等价无 gate(doc93 §8)。

## 场景(step → verify)

### E1 — 闭环主路:gate 持有 → 飞书浮出 → 真 approve → 真 fanout 解锁(**核心**)
1. `plan_approval_enabled=true`,真 Orchestrator;`task_map.ready`(2 tasks)→ `run_once`。
   → verify: **无 `fanout.started`**(gate 持有);有 `plan.approval.requested`,`task_count==2`,带 `plan_id` + digest/task_map ref。
2. 飞书浮出:`push_plan_approval_cards_once(MockTransport, action_secret)` → verify: 发出 1 张卡,含 `plan_id` + deep-link + **签名 approve 按钮**(A2)。
3. operator 经**真 ControlledAction**(`source=feishu`,飞书/Web 同款路径)`plan-approve` → verify: `plan.approved`,`actor=operator`,`via` 含 controlled-action,**无 `auto`**。
4. `run_once([plan.approved])` → verify: **出现 `fanout.started`**(gate 真解锁真实 writer fanout)。

### E2 — gate disabled:kernel auto-mint(无人工,行为等价)
1. `plan_approval_enabled=false` → `task_map.ready` → `run_once`。
   → verify: 直接 `fanout.started`;`plan.approved.payload.auto == True`(免审直发的审计判别)。

### E3 — reject:经真 ControlledAction 驳回 → 持有不解锁
1. enabled,持有后,真 ControlledAction `plan-reject`(reason 必填)→ `plan.rejected{reason}`。
2. `run_once` → verify: **仍无 `fanout.started`**;reject reason 可审计落库。

### E4 — 幂等 / 不越权
1. E1 approve 后,再 `run_once([同一 plan.approved])` → verify: **不二次孵化**(无第二条 `fanout.started`)。
2. approve 前任何时刻 → verify: 始终无 `fanout.started`(gate 不被绕过)。

## 通过标准(对齐 doc 93 §9/§11)

- gate enabled 时:不 emit `plan.approved` 永不孵化;经真 ControlledAction approve → 孵化(B15/§9)。
- disabled → auto-mint,`auto:true` 审计判别(B14/§8)。
- reject 后保持 held,reason 可审计(B14)。
- 全程飞书只 notify + 请求 mutation,plan.approved 的 actor 恒 operator,无第二控制面。
- `pytest tests/test_feishu_ai_native_loop_e2e.py` 可重复跑,无真凭证。
