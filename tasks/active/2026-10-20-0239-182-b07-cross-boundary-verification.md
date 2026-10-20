# 182-B07 P1 验收 · 从规划交接到分层验收的回归闭环

> 状态: active
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

- 采用设计 182 推荐方向；公共契约修改先由实际 handoff 回归确定。
- 行为改动先补失败回归，再实现并记录聚焦验证；方法变更验证注入及示例。
- 完成状态以实际证据为准；产品 Plan 复审不授权产品 writer。
