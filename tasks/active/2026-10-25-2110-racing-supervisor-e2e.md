# 赛车小游戏监工验收与通用缺陷修复

> 状态: active
> 用户已授权生成全面 Prompt 并立即执行真实端到端测试、持续修复；具体 Plan 实现审批保留给用户。

执行依据：[监工 Prompt](../../docs/runbooks/2026-10-25-2110-racing-supervisor-e2e-prompt.md)。基线 `8367e6c`，工作分支 `wip/codex-20261025-ai-sdlc-graph`。

持续记录：[真实运行与修复](../../docs/records/2026-10-25-2110-racing-supervisor-e2e.md)。

## 验收

- [x] 在 8002 用 Playwright 新建赛车项目并输入需求 -> verify: 真实 Provider、Kanban 与 Workflow AskUserQuestion、Task/Request 来源和完整 YAML Preview 可核验；旧提案误点及受控恢复已记录。
- [ ] Workflow 和 Plan 两处真实局部 Chat -> verify: 不可变候选、局部 diff、新版本绑定及重审均有效。
- [ ] Plan 交付用户后暂停 -> verify: 用户批准具体版本前没有 impl 派发，批准后自动继续。
- [ ] 设置 3 lanes 并观察 impl/verify -> verify: 两类分别有至少两个 lane 的真实重叠区间；未达到时有根因、修复及实际重验。
- [ ] 赛车产品与六视角图闭合 -> verify: 独立验证、Judge、内核 Run/Task/Goal 终态一致，当前候选的浏览器产品验收通过。
- [ ] 导出证据、提交修复并清理 -> verify: 准确验证层级、秘密检查、临时资源清理与干净工作区审计；不 merge/push。
