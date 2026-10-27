# Plan Agent 与 Preview / Raw 对齐

用户要求引用编辑的 Agent 留在 Preview / Raw 工具栏附近，避免出现在长文档底部。

## 变更

- `PlanReviewDocument` 接收 Agent 控件，工具栏按 Preview、Raw、Agent 排列。
- Plan 回复框锚定在工具栏下方，覆盖局部正文而不推移文档；继续共用 YAML 的回复框组件。
- 保留选区引用、关闭重开、局部 Diff、Apply/Discard 和原审批锁定机制。Workflow 与 Inbox 共用此行为。

## 验证

- 前端生产构建、bundle budget、`dev-verify.py run --base dev --paths ...` 的 typecheck 与前端单元测试通过。
- Docker Playwright：Workflow 两项通过；Inbox 首轮十项通过，一项因标题与正文出现相同 Plan ID 导致定位不唯一。将断言限定在标题工具栏后，该项重跑通过。共十三项相关浏览器用例最终通过，原失败日志保留。
- 8002 的真实 `ty-review-42cb2551` / `evt-38c86009183e`：鼠标选段 → 引用到 Agent → headless 生成局部候选 → Diff → Discard。候选仅替换指定短语，引用外文本保持不变；未 Apply、未批准，Plan 仍为 pending。
- 桌面回复框距工具栏底部 8px，宽 440px；390px 窄屏距工具栏底部 8px，宽 362px，Preview / Raw / Agent 同排，无横向溢出。打开回复框未改变正文位置。
- 8002 已提供更新后的静态资源；8003/8004 夹具已记录 `simulation.done` 并停止，临时状态已清理。

证据：[桌面](2026-10-27-plan-agent-toolbar/03-quoted-agent.png)、[真实局部 Diff](2026-10-27-plan-agent-toolbar/04-real-agent-diff.png)、[手机](2026-10-27-plan-agent-toolbar/05-mobile-agent.png)、[实测结果](2026-10-27-plan-agent-toolbar/result.json)、[实测脚本](2026-10-27-plan-agent-toolbar/live.mjs)、[构建](2026-10-27-plan-agent-toolbar/build.log)、[前端检查](2026-10-27-plan-agent-toolbar/verification.log)、[Workflow 回归](2026-10-27-plan-agent-toolbar/workflow-browser.log)、[Inbox 首轮](2026-10-27-plan-agent-toolbar/inbox-browser.log)、[定位修复重跑](2026-10-27-plan-agent-toolbar/inbox-recheck.log)、[清理](2026-10-27-plan-agent-toolbar/cleanup.json)。

本轮是 UI 验证，未运行后端全量测试，也未执行提交或合并。
