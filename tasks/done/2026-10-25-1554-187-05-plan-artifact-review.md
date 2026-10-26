# Plan/Scan PRD 与计划产物完整预览和 Agent 局部修订

> 状态: done
> 实现: `d4782e9 feat: add evidence-bound workflow graph workbench`
> 来源: [设计 187](../../docs/design/187-ai-sdlc-graph-workbench.md)；用户验收 b；187 §5、§8、§9。

## 实施范围

- 在 Workflow 阶段/Inspector 显示生成的 PRD/设计/Plan 文档及来源版本。
- 支持引用段落讨论/局部修改、差异确认、受控修订与必要的重新规划/审批。
- 保护已接纳不可变工件和运行合同，修改不能静默覆盖执行中的目标。

## 验收

- [x] Plan/Scan 真实生成 PRD 后打开预览并引用一段要求修改 -> verify: 完整正文可读，局部修改有差异和新修订。
- [x] 已接纳或已运行计划收到修订 -> verify: 原证据保留，变更通过受控重新规划/审批，不直接覆盖 canonical artifact。

## 执行记录

- 2026-10-25：用户明确批准基于 187 创建新 worktree、生成 backlog 并实施；真实 Provider / Playwright 验证已授权。

- 验证：真实 Plan 引用片段修改后生成不可变候选；受控 reject/replan，Planner 重生成、Critic 通过后浏览器逐字核对全文再批准。API token、旧草稿、Unicode、幂等测试通过。 详见 [实施记录](../../docs/impl/187-ai-sdlc-graph-workbench.md) 与 [真实 E2E 记录](../../docs/records/2026-10-25-187-workflow-graph-e2e.md)。

- 收尾验证：真实 Provider 完成、Docker Playwright 3 passed；图/Goal/恢复闭包 72 passed；Web typecheck/模型测试、51 项 sentinel、10 项 flow-smoke 通过。其他影响闭包与环境重跑分别记录于 [实施文档](../../docs/impl/187-ai-sdlc-graph-workbench.md)。未运行全量 pytest。
- 实现提交后工作区审计无残留；临时 E2E 服务、状态及本批依赖软链接已清理。归档提交仅包含本批 8 项任务。
