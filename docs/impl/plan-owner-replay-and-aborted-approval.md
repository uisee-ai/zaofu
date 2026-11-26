# Owner 需求修订重放与失效审批

关联：[199](../design/199-worker-failure-recovery-and-role-handoff.md)、[本次修复](../../tasks/2026-11-08-1357-plan-owner-replay.md)。

初次 Plan Package 准入会用受控 Owner 修订替代旧的 requirement ref/inline body；同一代重放现在采用相同的权威解析和同份事件快照，再比较有效正文。新 Owner 修订使有效正文变化时，旧代仍不能复用；其他显式端口仍逐一校验，不忽略未知或改变的输入。

kernel 的 fanout.cancelled 若 failure_scope=plan_admission，且 trigger_event_id 与 causation_id 一致，该 ready event 对应的审批退出 pending 和已批准等价复用集合。CLI、Web 及恢复等待使用同一投影；批准不能使原已取消准入恢复有效。原始取消/批准记录保留，修复后由 ControlledActionService 重新触发输出，再走正常准入及审批。新的 trigger 不继承旧审批权威。

该修复不改变 Plan Package reducer 对取消准入的隔离，也不修改任务要求、Owner 决定、完成成果或 active worker 身份。

## 业务审批前纠正任务派生的验收声明

已签发的 Task Map 可能把对原需求的误读写成逐任务 AC。没有显式 `goal_claims` 时，内核也会将这些 AC 固化为 mandatory Goal 声明。Owner 可在已获准的阶段恢复代内，通过带操作令牌的 `POST /api/actions/stage-replan-new-generation` 补录一次精确纠正。顶层提供 `workflow_run_id`、`stage_replan_generation`、原 `escalation_event_id`、本次 `approval_ref` 和 `derived_claim_correction`。纠正记录包含下列字段。

| 字段 | 校验 |
|---|---|
| `package_digest`、`claim_set_sha256` | 当前已准入 Plan 包及包内 claim-set 端口的 SHA-256 |
| `goal_claim_id`、`before`、`after` | 同一任务派生的 mandatory 声明及实际旧文、核准新文 |
| `operation_request_event_id` | 同一 Run、同一阶段恢复代、绑定该包和该 claim-set 的 Planner 请求 |
| `requirement_sha256`、`quote` | 该请求冻结的可读原需求来源 SHA-256 与原文逐字引句 |

Owner 负责判断引文与修订含义。服务校验身份、状态、字节和绑定，在运行账本发布唯一的受控回执。对同代、同包、同一声明的重复或冲突操作返回 409 并指向既有回执。已进入当前业务 Plan 审批、曾正式获批或已终态的 Run 走相应的正式评审路径。正式 Requirement 或显式全局声明的变更仍走既有 Owner 需求修订。

新签 Planner 和独立 Critic 从输入索引读取必读的 `critical-owner-claim-corrections`，可核对 Owner 请求及签收事件、原始引文和前后文本。已签 WRC 重放保持首次签发的资料。旧稿仍保留旧值时，预检按照正常流程要求返工。新稿必须在原任务保留该 mandatory 声明并采用核准值，随后重新生成 Task Map 和 Goal claim-set 两个不可变端口。Writer、Judge 和 Goal 消费实际准入的新包。修改未点名声明、挪到其他任务、改写正式需求或使用旧稿完成交付，均不在该纠正的授权范围内。
