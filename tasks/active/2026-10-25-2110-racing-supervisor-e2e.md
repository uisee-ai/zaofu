# 赛车小游戏监工验收与通用缺陷修复

> 状态: active
> 用户已授权生成全面 Prompt 并立即执行真实端到端测试、持续修复；具体 Plan 实现审批保留给用户。

执行依据：[监工 Prompt](../../docs/runbooks/2026-10-25-2110-racing-supervisor-e2e-prompt.md)。基线 `8367e6c`，工作分支 `wip/codex-20261025-ai-sdlc-graph`。

持续记录：[真实运行与修复](../../docs/records/2026-10-25-2110-racing-supervisor-e2e.md)。

用户已明确批准Plan `evt-adf3807d914c`，正文摘要 `c12ce3ef9dd16ba97c60259ba3ee75dca12d19d8397f67f5127f6546f9d2cc28`。[审批摘要与完整文档](../../docs/records/187-racing-supervisor-e2e/approval-review.md)保留审批前快照。唯一 `plan.approved=evt-158a8630051c` 于2026-10-26 00:01:28 UTC产生；因等待期误阻塞而修复、受控恢复后，00:09:01自动派发FOUNDATION。8项前序修复已提交：`bd11edf fix: harden workflow preparation and plan review gates`。本任务保持active，继续核验Impl/Verify并行与最终交付。

## 验收

- [x] 在 8002 用 Playwright 新建赛车项目并输入需求 -> verify: 真实 Provider、Kanban 与 Workflow AskUserQuestion、Task/Request 来源和完整 YAML Preview 可核验；旧提案误点及受控恢复已记录。
- [x] Workflow 和 Plan 两处真实局部 Chat -> verify: 不可变候选、局部 diff、新版本绑定及重审均有效；Plan两次修订经真实Planner/Critic接纳，第二次新增AC-A07进入绑定合同，原15项保留。
- [x] Plan 交付用户后暂停 -> verify: 用户批准具体版本前没有Impl派发；批准后发现审批等待误触发Run blocked，回归修复并经正式接口恢复，同一Plan自动继续，没有重复批准或另建任务图。
- [ ] 设置 3 lanes 并观察 impl/verify -> verify: 两类分别有至少两个 lane 的真实重叠区间；未达到时有根因、修复及实际重验。
- [ ] 赛车产品与六视角图闭合 -> verify: 独立验证、Judge、内核 Run/Task/Goal 终态一致，当前候选的浏览器产品验收通过。
- [ ] 导出证据、提交修复并清理 -> verify: 准确验证层级、秘密检查、临时资源清理与干净工作区审计；不 merge/push。
