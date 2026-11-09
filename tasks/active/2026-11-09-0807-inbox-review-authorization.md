> 状态: active

# Inbox 收敛为人类审核与授权中心

用户已于 2026-11-09 授权：基于 dev 建立独立 worktree，创建 backlog 并实施，完成后进行真实验收。
基线：`052e6a40`；分支：`wip/codex-20261109-inbox-review`。

## 批次范围

1. Inbox 只保留 `plan_approval` 和有明确对象、版本及现有处理入口的 `approval`。
   移出 `human_decision`、`channel_decision`、`kanban_question`、`owner_attention`；
   不删除来源事件、不代答、不自动批准或解除阻塞。无动作的旧 approval 留在来源诊断入口。
2. 已存在的 Workflow 启动/配置提案作为 `approval` 展示，复用原提案身份与审核入口；
   原 Kanban/Channel 提问仍在原会话回答。审批身份、版本、已读与执行状态保持分离。
3. 前后端和 SQLite/完整事件回退采用一致口径；列表仅保留审核/授权与历史视图，
   过期、撤销、拒绝、已处理、已替代事项退出待办。同步更新设计说明。
4. 完成影响闭包测试、构建、Docker 浏览器真实后端验收，以及隔离真实 Provider 验收；
   分别记录 fixture、mock 和真实 Provider 的证明范围，不将批准或卡片消失当作任务完成。

5. 真实验收暴露 Plan 同代回放身份补齐不一致：补共享机械身份绑定和不变/改变正文回归，
   保留原内容围栏，从持久化状态继续验收；不调整语义验收或质量门。

## 验收

- 混合新旧消息进入事件账本 -> verify: Inbox 仅保留有效审核/授权及其历史，四种移出类型不计入列表、待办、未读；源事件完全保留。
- 旧无动作 approval -> verify: 不进入 Inbox；来源诊断仍可发现，无伪造 resolved/approved。
- Plan / 重规划审核 -> verify: 可读预览及受控批准/拒绝继续有效，旧版本和重复决定被拒绝或幂等处理。
- Workflow 提案 -> verify: Inbox 与源页面关联同一请求/版本；审核最终配置后提交，后续状态由原工作流事实推导。
- Kanban/Channel 提问 -> verify: 原入口可见等待、可回答，回答写回正确请求且下游继续；Inbox 不制造问答待办。
- 已读及历史 -> verify: 阅读不减少待审批数；新版本重新未读；终态退出待办且保留历史。
- SQLite 与完整事件 -> verify: 相同来源产生相同事项、版本、状态及计数。
- 真实验收 -> verify: 隔离浏览器访问实际 API，记录审批和问答结果；实际 Provider 场景记录调用、工作流启动及终态证据，清理临时进程和运行状态。

## 实施与证据

范围 1–3 已完成。验证：主闭包 206 passed、扩展闭包 173 passed（含重复 mock E2E）、
最终投影 38 passed、sentinel 53 passed、Web 构建与 unit 通过、浏览器回归 17 passed、Traces 2 passed。
实际后端 Docker 验收 4 passed，包含从侧栏 Traces → Operations 找到并处理移出记录。
真实 Codex 已完成提问/回答/创建 Task/准备 Workflow，从 Inbox 审核 Raw 后启动成功；
Task `TASK-349699` 仍在实际执行，终态验收未完成，保持 active。

详见 [实施记录](../../docs/impl/2026-11-09-0807-inbox-review-authorization.md)。
审批范围仅限此批次；不新增通用预算/权限平台，不修改其他项目运行状态，不合并或 push。
