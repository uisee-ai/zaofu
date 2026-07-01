# 测试计划 — feishu ↔ channel / kanban-agent 端到端验证(凭证无关)

> 目标:在**无真飞书凭证**下,验证我们这几轮建的 feishu 集成(A 计划审批闭环 / B 回调信任模型 /
> C Delivery Projector + Interrupt / A2 签名动作 token)能端到端**驱动并消费 channel + kanban-agent
> 的真实事件契约**。用 `MockFeishuTransport` + 真 CLI 入口 + 闭环 token 往返。
> 实现:`tests/test_feishu_channel_kanban_e2e.py`(随套件跑,永久回归)。
> 边界:真飞书 live 投递(真卡进群、真 agent 在 channel 回复)需 app 凭证 + 活 harness,不在本计划。

## 被验证的真实路径

```
channel/plan/kanban 事件(events.jsonl,真契约)
  → zf feishu push --transport mock        [出站:Plan 卡 / Delivery 卡 / channel 投影]
  → 卡片内联按钮(A2 签名 token)
  → zf feishu handle(入站回调)             [B 身份门 + A2 token 门]
  → ControlledAction → plan.approved / agent.session.run.cancelled
```

## 环境(fixture)

- tmp state_dir + `zf.yaml`:`integrations.feishu_identity` = `enabled:true`,
  `require_signed_actions:true`,`users: {ou_boss: approver, ou_op: operator}`,
  secret 经 `ZF_FEISHU_ACTION_TOKEN_SECRET` env 注入。
- 事件用真 `EventWriter` 写;出站用真 CLI `main(["feishu","push",...])` 或 library
  `push_*_cards_once(MockFeishuTransport)`(后者可内省签名卡片)。

## 场景(step → verify)

### S1 — 计划审批出站 + ledger 幂等(A)
1. emit `plan.approval.requested {plan_id:P1}` → `main(feishu push --transport mock --to oc_chat)`
   → verify: stdout `plan_cards_sent=1`;`plan_approval_ledger.json` 有 `plan-approval-P1` state=pending。
2. 重跑 push → verify: `plan_cards_sent=0`(ledger 幂等,不重发)。

### S2 — 签名按钮闭环放行(A+B+A2,**核心闭环**)
1. library push Plan 卡(`action_secret` 注入)→ 从 MockTransport 取回卡片 → 提取 `value.t`(真签发 token)。
2. 用**该 token** 造 `ou_boss`(approver)的 button 回调 → `_handle_event_data`
   → verify: 写出 `plan.approved {plan_id:P1, surface:feishu}`,actor=operator。

### S3 — 篡改/越权被拒(B+A2 fail-closed)
1. 拿 S2 的 token 改 action 目标为 `plan-approve:P-EVIL` → verify: rejected `token.target_mismatch`,无 `plan.approved`。
2. `ou_op`(operator,低于 APPROVER)持有效 token 点 approve → verify: rejected(B 身份门),无 mutation。
3. `require_signed_actions:true` 下裸按钮(无 token)→ verify: rejected `token.token_required`。

### S4 — Delivery Projector 折叠 channel reply 生命周期(C)
1. emit `channel.agent.reply.requested/started {request_id:R1}` → push
   → verify: Delivery 卡 **Working**(含 Interrupt 按钮),`delivery_ledger.json` R1=working。
2. emit `channel.agent.reply.completed {request_id:R1}` → push
   → verify: **原卡原位 update 为 Done**(updated=[R1]),不新发。
3. 高频:再 emit 50 条 `agent.session.part.delta {request_id:R1}` → push
   → verify: 无新发/无 update(delta 不投影,不刷屏)。

### S5 — Interrupt 闭环 + nonce 单次(C+A2)
1. push Working 卡 → 取回 Interrupt 按钮 token(`agent-cancel:R2`)。
2. `ou_op` 用该 token 点 Interrupt → verify: 写 `agent.session.run.cancelled {request_id:R2, source:feishu}`,**无 pane/pid kill 事件**。
3. **重放同一 token**(同 nonce)→ verify: rejected `token.nonce_replay`,不二次取消。

### S6 — channel 消息投影(channel 双向出站半边)
1. emit `human.escalate` / `channel.message.posted` → push(带 `--channel`)
   → verify: MockTransport 收到对应投影消息(ProjectionRouter 路由)。

## 通过标准

- 全部场景 step→verify 断言绿;`events.jsonl` 里出站/入站/裁决事件链完整;
- 闭环证明:**我们 push 出去的签名按钮能被点回来并生效;篡改/越权/重放/过期被 fail-closed 拒绝**;
- Delivery 折叠对 delta 零放大;无 tmux/pid kill。
- 随 `pytest tests/test_feishu_channel_kanban_e2e.py` 可重复跑,无真凭证、无残留(tmp 自动清)。
