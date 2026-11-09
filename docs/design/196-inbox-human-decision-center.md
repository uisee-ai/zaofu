# 196 Inbox 人类审核与授权中心

> 状态：implemented — 准入与来源迁移已接入；验收层级与真实 Provider 结果见实施记录。
> 更新：2026-11-09。替代 2026-10-30 将普通问题和运行处置纳入 Inbox 的旧准入范围。

## 1. 定位

Inbox 收集需要人审核具体方案或授予明确权限的事项。每条事项必须回答：审核什么、
授权什么、对应哪个版本、在哪里决定、决定后由哪个既有业务流程继续。
普通问答留在 Kanban / Channel 会话；运行处置和旧记录核实留在 Traces → Operations（旧 Runtime 路由兼容）。

Inbox 是只读投影，不拥有审批、Task、Run 或问答的 canonical 状态。原事件、必要产物、
受控动作和来源状态机仍是事实来源。移出 Inbox 不会代答、批准、拒绝、取消、重试或解除阻塞。

## 2. 准入与分流

| 来源 / 内容 | Inbox | 来源处理入口 |
| --- | --- | --- |
| `plan.approval.requested` | `plan_approval` | Plan 可读预览、批准、拒绝、引用编辑 |
| `approval.requested` 的有效重规划合同 | `approval` | 现有 Plan 预览与 `replan-approve/reject` |
| `workflow.request.proposed` 的有效提案 | `approval` | 同一 Request 的 Workflow 审核页面 |
| `human_decision` | 不进入，包括历史 | Operations 展示原请求和原有受控处置操作 |
| `channel_decision` | 不进入，包括历史 | Channel 的问题卡及其等待状态 |
| `kanban_question` | 不进入，包括历史 | Kanban Agent 的问题卡及其等待状态 |
| `owner_attention` | 不进入，包括历史 | Operations 保留来源和待核实状态 |
| 无对应处理器 / 合同不完整的旧 `approval` | 不进入 | Operations 保留待核实记录，不生成虚假按钮 |
| 普通进度、结果、告警、投递故障 | 不进入 | 原任务、Workflow、Runtime、投递状态 |

重规划准入沿用现有可执行动作合同：`owner_route=plan_synth_owner_decision`、proposal / eval
引用和已登记的动作名称。执行时仍由受控动作核对当前请求与实际业务约束，不因为卡片
显示为审批就获得执行权限。未来预算 / 工具权限授权需要先有来源业务合同和真实处理器，
本次没有新增通用预算或权限平台。

Workflow 提案必须能通过既有 sidecar digest / schema 校验，绑定相同 Request / revision / digest，
且 `approval_status=approvable`。摘要读取经过身份与 digest 校验的 Requirement 正文。
缺失、损坏或不可批准的提案不伪装成审批，准备失败在原 Workflow 页面核对。

同一 Workflow 请求在 Inbox 和源页面共享身份，不新建审批对象。Inbox 的“Review workflow”
打开该 Request 的原审核页面；用户在源页面审阅当前配置、Raw 或修改后的版本后提交。
Inbox 不提供绕过方案审核的一键启动。来源仍负责并发、过期版本、配置应用与启动的校验。

## 3. 生命周期、身份与历史

- Plan 保留现有 `plan_id` 及审批请求事件绑定；语义采纳不能替代人工批准。
- 重规划保留 `approval_ref`、请求事件、checkpoint、proposal / eval 等绑定。
- Workflow 项目身份为项目作用域内 `approval:workflow:<request_id>:r<revision>`，
  绑定精确 proposal ref / digest。更高 revision 将旧的待审批项标为 superseded；
  新版本成为独立未读项，不能沿用旧版本的授权或阅读回执。
- 批准、拒绝、过期、被替代等事实来自原始事件，退出待办后保留审批历史。
  已处理项不再提供批准 / 拒绝控件。
- Workflow 的“已批准”与 `execution_status=submitted/running` 分开显示；运行终态和产物
  在原 Workflow 页面查看。提交或授权通过不代表任务完成。
- Run Manager 的 acknowledged 只表示收到决定；Operations 继续显示 processing、applied、
  failed 等来源状态，不能把卡片消失或收到事件当作恢复成功。
- 历史未知记录不自动关单；没有可靠合同的记录保留待核实说明和源身份。

## 4. 已读与计数

阅读状态按项目共享，独立于业务决定：

- 用户主动查看或明确执行全部已读，才记录版本化阅读回执。默认选中、轮询不自动已读。
- Inbox 待办数只计 pending 审核 / 授权项；未读徽标只计其中未读的版本。
- 已读不减少待审批数。新版本、实质性方案变化重新未读；重复投递不反复提醒。
- 全部已读绑定操作时看到的 item / version 集合，不覆盖稍后到达的新版本。
- 历史不计待办和未读；打开审批历史不会恢复操作权限。
- SQLite 与完整事件回退复用同一投影；读取错误不能伪报空队列。

API schema 更新为 `operator-inbox.v3`。为旧客户端保留旧计数字段并返回 0；items、pending、
views 均不再包含被移出的消息类型。前端只提供审核 / 授权与历史入口，类型筛选仅有
`plan_approval`、`approval`。阅读身份仍没有扩展成多用户系统。

## 5. 来源可操作性与迁移

迁移通过读取现有事件重新投影完成，不写迁移事件、不改写账本、不批量已读或 resolved。
原 Channel / Kanban 消息、问题与答案均保留，回答继续交回原身份与版本对应的 Agent。

Run Manager 人工处置及旧审批记录通过 Operations 的 `source_requests` 只读切片呈现。
原有 Diagnose / Halt / Dismiss / 受控动作授权仍调用原 Web token 门禁和业务处理器。
前端显示提交中与失败，后台再次核对当前合同；来源不能执行的动作不会因迁移获得处理器。

投递路由缺失与失败继续使用 Operations 的通知投递状态；不停止已配置的外部渠道投递，
也不将每条投递失败重新包装成 approval。旧项目需要升级服务后才采用新视图，本次隔离验收
不宣称已经迁移或处理真实业务项目。

## 6. 实现与验收

主要实现：

- [Inbox 与运行请求投影](../../src/zf/runtime/operator_inbox.py)
- [Workflow 审批投影](../../src/zf/runtime/operator_workflow_approvals.py)
- [版本化阅读](../../src/zf/runtime/operator_inbox_read.py)
- [SQLite 查询](../../src/zf/web/projections/read_model.py)
- [Operations 接口](../../src/zf/web/observability.py)
- [Inbox 页面](../../web/src/components/delivery-trace/PlanApprovalPanel.tsx)
- [运行处置页面](../../web/src/components/observability/RuntimeRequests.tsx)

验收闭包：

1. 混合来源回放 -> verify: 仅保留有效审核 / 授权；其余来源可发现，事件数不变。
2. 原 Channel / Kanban 回答 -> verify: 正确来源收到答案并继续，Inbox 不产生问答待办。
3. Plan / 重规划 / Workflow 决定 -> verify: 请求身份与版本一致，历史不提供失效动作。
4. 已读、新版本、重复投递 -> verify: 阅读与审批计数独立，SQLite 和全量事件一致。
5. Operations 原处置动作 -> verify: 保留 token 门禁和后端 currentness 校验，提交不伪报应用成功。
6. Docker 浏览器访问实际 API -> verify: 列表、徽标、历史、原入口问答和来源迁移可操作。
7. 真实 Provider -> verify: 单独记录实际提问、回答、提案、批准、启动、任务终态与产物证据；
   fixture / mock 不能替代该证明。

可重跑浏览器夹具入口：[run_inbox_review_acceptance.py](../../tests/e2e/run_inbox_review_acceptance.py)。
夹具使用隔离 `/tmp/zf-inbox-*`、8002 以上端口，结束写入 `simulation.done`，保留证据并清理运行状态。

完整命令、结果和资格边界见 [实施记录](../impl/2026-11-09-0807-inbox-review-authorization.md)。
