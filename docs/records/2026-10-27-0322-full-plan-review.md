# 重试 Plan 引用编辑恢复全文

## 原因

`ty-review-42cb2551` 的待审 Plan `evt-192e7536a145` 是失败 writer 的恢复批次，重用了原有任务表、计划包和需求版本，但新的 `task_map.ready` 未携带 Plan 正文引用。此前将摘要作为 Agent 编辑文档只能修改摘要，并不能修改原有 PRD / SDD / TDD。当前任务的原 Plan 已在 `evt-38c86009183e` 审批通过，完整正文仍存在且其 SHA-256 与审批事件一致。

## 修改

- 通过同一 run 的已批准 Plan → writer fanout → 失败事件 → 重试事件恢复正文引用；要求任务表路径、generation、计划包及需求身份一致，并重新校验正文和任务表摘要。事件发生顺序不匹配、旧计划未批准、身份不一致或文件变更均不继承。
- 新的失败子任务恢复事件直接携带可验证的原 Plan 正文引用和摘要；已有恢复事件由阅读投影基于历史事件还原，不改写事件账本。
- 只提供摘要的 Plan 保留格式化只读预览，不再把摘要作为完整 Plan 交给 Agent 修改。显式选择的其他附件仍按原有接口处理。
- 局部修改仍生成不可变候选，经用户 Apply 通过受控退回触发重新规划，不直接覆盖正文或批准 Plan。

## 验证

- [来源链单测](2026-10-27-full-plan-review/lineage-final.log)覆盖历史回溯、新恢复事件、Agent 候选及受控 Apply、不同 run / 任务表 / generation / 计划包 / 需求、正文与任务表篡改、拒绝和迟到批准；[相关模块 55 项通过](2026-10-27-full-plan-review/focused-corrected.log)。
- [Docker Playwright 7 项通过](2026-10-27-full-plan-review/browser.log)，覆盖无全文时只读、完整文档、格式化表格和引用、Agent 候选与审批锁定；[构建](2026-10-27-full-plan-review/build.log)和 [premerge gate](2026-10-27-full-plan-review/gate.log)通过。
- 真实 8002 项目的当前 Plan [在 Inbox 和 Workflow、桌面和手机通过](2026-10-27-full-plan-review/result.json)：`full_plan`，完整正文 13315 字符，Preview / Raw 正文一致。[截图](2026-10-27-full-plan-review/inbox-1440-formatted.png)。
- 真实 headless Agent 引用全文的 PRD 标题，生成仅变更该标题的 Diff；[截图](2026-10-27-full-plan-review/live-agent-candidate.png)、[结果](2026-10-27-full-plan-review/agent-result.json)。候选已 Discard；未 Apply 或 Approve，当前 Plan 仍 pending，正文不变。
- [影响闭包回归](2026-10-27-full-plan-review/verification.log) 975 项通过、1 项失败；失败的 `test_events_exposes_malformed_and_cursor_paginates` 在并行运行时读取测试事件返回空列表，[单独复跑通过](2026-10-27-full-plan-review/web-server-retry.log)，其原因未进一步确定。这不是全库测试。回归因失败未执行前端单元步骤，已[单独运行](2026-10-27-full-plan-review/unit.log)。首轮新增字典事件测试误用 Pydantic 方法，已改为 dataclass 序列化，[失败记录](2026-10-27-full-plan-review/focused-final.log)保留。

8002 已重启，保持 `0.0.0.0` 与 action token；测试用 8003 已发出 `simulation.done` 并清理。未 commit / merge / push。
