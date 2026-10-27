# Plan 摘要引用编辑的 Markdown 入口修复

## 复现与原因

真实 8002 项目 `ty-review-42cb2551` 的当前待审 Plan `evt-192e7536a145` 来自失败任务重试，仅绑定 `digest_ref`，没有 `accepted_plan` 正文。旧入口直接选择 `artifacts[0]`，因此从 Markdown Preview 切到引用编辑时打开了 `acceptance_matrix` JSON。之前验证的 `evt-38c86009183e` 有完整 Markdown 正文，不能覆盖此情况。

[修复前证据](2026-10-27-plan-digest-reader/before.json)记录了实际选择的附件与正文；[修复前截图](2026-10-27-plan-digest-reader/before.png)显示标题数量为零的整段 JSON。

## 修改

- 将审批事件明确绑定的 Markdown 摘要作为 `plan_digest` 只读投影产物暴露，绑定 ref 和 SHA-256，保留 `digest_only` 身份。
- 引用编辑优先打开完整 Plan，其次打开“Plan 摘要”；无绑定 Markdown 时保留格式化预览并说明引用不可用。其他附件由用户主动选择。
- 切换文档恢复 Preview；已有 Markdown 分段格式化、源位置引用与 Agent 弹窗保持使用。
- 摘要局部修改沿用受控退回与重新规划；不直接写原摘要、不批准 Plan、不修改 Task 状态。源摘要变化后，旧候选不能应用。

## 验证

- 构建、TypeScript 与 bundle budget [通过](2026-10-27-plan-digest-reader/build.log)；前端单元测试[通过](2026-10-27-plan-digest-reader/unit-final.log)。
- `dev-verify.py` 选择 23 个 Python 测试模块，首轮 611 通过、2 个新增测试失败：测试误认为审阅 context API 包含内部 preview。改从正式 Preview API 检查后，相关两个模块 [36 项全通过](2026-10-27-plan-digest-reader/backend-final.log)。[规划](2026-10-27-plan-digest-reader/verification-plan.log)、[首轮记录](2026-10-27-plan-digest-reader/verification.log)保留；这是影响闭包回归，不是全库测试。
- Docker Playwright 七项最终通过：摘要格式化与引用候选、缺少 Markdown 时不自动打开 JSON、三个窗口尺寸、长表格源位置引用、原有正文 Agent/审批锁定。[首轮](2026-10-27-plan-digest-reader/browser.log)为六过一败，失败来自提示文本同时匹配可见提示和隐藏 Agent 内容；限定 status 后[该项通过](2026-10-27-plan-digest-reader/browser-retry.log)。构建完成前误启动的一轮已中止，未作为验证证据。
- 真实 8002 的 Inbox / Workflow 两入口及桌面 / 手机 [全部通过](2026-10-27-plan-digest-reader/result.json)：默认 Markdown、表格与清单、Raw 原文一致、正文填满可用区域、Agent 不挤压正文。旧脚本的固定高度阈值不适用于摘要提示，改为核验实际容器底部和剩余阅读空间后通过。[桌面](2026-10-27-plan-digest-reader/inbox-1440-formatted.png)、[手机](2026-10-27-plan-digest-reader/inbox-390-formatted.png)。
- 真实 headless Agent 将引用标题修改为“Checklist（机械预检，待人工审核）”，只改变引用片段并显示 Diff。[截图](2026-10-27-plan-digest-reader/live-agent-candidate.png)、[结果](2026-10-27-plan-digest-reader/agent-result.json)。测试候选已 Discard，未 Apply / Approve，原 Plan 仍 pending、正文不变。

8002 已重启，保持 `0.0.0.0` 和原 action token；8001 未改变。临时 8003 已发出 `simulation.done` 并清理。未 commit / merge / push。
