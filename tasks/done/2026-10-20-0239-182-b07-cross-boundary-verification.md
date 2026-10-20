# 182-B07 P1 验收 · 从规划交接到分层验收的回归闭环

> 状态: done；实现：`8bbe001 fix: align plan review summaries and long document scrolling`。
> 来源: [设计 182](../../docs/design/182-plan-review-task-slicing-and-evidence-closure.md) B07。
> 授权: 用户明确要求在独立 worktree 生成 backlog 并全部实施，随后启动 Dashboard 重新规划和评估。

## 范围与验收
- 对应：公共修复是否实际串通；所有者：Runtime/Web 验证；依赖：B01、B02，以及 B06 的重规划样例。
- 使用最小、无秘密、与本机路径无关的 fixture 重放审批链 → verify: 完整正文和同一版本的矩阵可读，人工门未被绕过。
- 模拟局部任务完成及后续候选验收 → verify: 允许正确阶段前进，后续缺证据仍阻止完整完成，旧目标证据不可复用。
- 运行相关确定性 mock E2E → verify: 使用真实 Runtime 入口和受控 writer；清楚标记 mock，不替代产品数据库或真实 provider 证明。
- 补一次 Docker 浏览器验证 → verify: 真页面展示与 API 一致，长文可读，无因打开 Preview 触发的写操作。
- 各实现 backlog 自带测试；本项负责跨边界回归，不是前面任务延后补测试的理由。

## 实施记录


## 完成证据

- 共享阶段准入补充：`2353c80 fix: enforce verification stage authority at result admission`；
  reader caller 补充：`9383191 fix: preserve bound candidate verification routes in readers`。
- 正式案例与综合评审：`e7d1cd1 docs: record verified Loshu replanning and coverage review`。
- 真实 Runtime/合同影响闭包最终 867 passed；最后的 CLI/摘要/API 修改相关调用者 230 passed；
  `bash scripts/dev-premerge-gate.sh` 51 passed；`bash scripts/run-flow-smoke.sh` 10 passed。
- `npm run build` 包含 typecheck/build/bundle budget，通过；Docker 下
  `playwright test tests/plan-preview-content.spec.ts` 4 passed，涵盖全文/附件、旧摘要、异步切换、
  超长 Unicode 分页和桌面/手机末行 Range 可见断言。文档/指令检查 9 passed。
- 实际 writer handoff、普通和 typed Verify 准入、reader candidate 路由及目标绑定回归共同验证：
  当前阶段可诚实完成，未来 mandatory 没有提前签收；完整候选仍需其实际证据。
- 新旧真实 Preview 都只读；最终 Plan 桌面/手机验证通过，浏览器证据不替代产品业务 E2E。
- 自动扩大验证曾有 38 个既有失败，已在原 dev 重现；手机 contract-health 溢出同样为基线。
  完整分类及未执行层级见实施记录，没有宣称全仓全绿或未来 FMS 产品已遵循 TDD。

- 详细评估：[综合评审](../../docs/records/2026-10-20-0327-plan182-loshu-replan-review.md)。
