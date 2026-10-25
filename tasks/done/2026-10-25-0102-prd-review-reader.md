> 状态: done
> 实现 commit: 1e044a7 feat: strengthen generic plan approval review contract

# PRD Preview 人类阅读导航

## 目标

让审批人能快速从 PRD 目标跳到 SDD、TDD、Decision、Task Map 和 trace，而不依赖连续滚动查找。

## 范围

- 在完整 Preview 中提供稳定章节目录和跳转。
- 展示 decision、task、trace 的数量及状态摘要；保留完整内容作为正文。
- Task Map、trace 和原始 authority 继续作为只读辅助证据，不改变审批状态。

## 验收

- step -> verify: `derived_plan_full` 页面显示 PRD/SDD/TDD/Decision/Task Map/Trace 导航和计数。
- step -> verify: 章节跳转、连续阅读、分页阅读和源码查看保持可用。
- step -> verify: Web build/typecheck/unit tests 通过，且不改变 approve 的受控 action 路径。

## 验证

- `npm run build` -> passed；bundle budget passed。
- `npm run test:unit` -> passed。
- `dev-verify` deterministic tier -> 1499 passed, 8 skipped；Web typecheck/unit passed；flow smoke（正确依赖环境）10 passed。
